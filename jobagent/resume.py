"""Parse the master resume once, then tailor + render a PDF per job."""
from __future__ import annotations

import json
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from jobagent.browser import pdf_page
from jobagent.config import MASTER_RESUME_JSON, OUTPUT_DIR, cfg
from jobagent.llm import structured, untrusted
from jobagent.models import Resume, TailoredResume

TEMPLATES = Path(__file__).parent / "templates"


PARSE_SYSTEM = """You convert resumes into structured JSON. Copy the candidate's wording exactly;
do not summarise, improve, or invent anything. Use null/empty lists for missing sections.
- headline: the tagline under the name, if any.
- experience.title is the job title only; put a team/product/platform line shown with it in `team`.
- Put a 'Core Competencies' section (if present) in skills with category exactly 'Core Competencies'.
- A combined 'Certifications & Awards' section: certificates -> certifications, awards -> achievements.
- Text that is bold inside the summary or bullets: wrap it in **double asterisks**."""


def parse_pdf(pdf_path: Path) -> Resume:
    resume = structured(
        Resume,
        system=PARSE_SYSTEM,
        content="Extract the attached resume.",
        pdfs=[pdf_path],
        effort="low",
        task="parse",
    )
    MASTER_RESUME_JSON.parent.mkdir(parents=True, exist_ok=True)
    MASTER_RESUME_JSON.write_text(resume.model_dump_json(indent=2), encoding="utf-8")
    return resume


def load_master() -> Resume:
    if not MASTER_RESUME_JSON.exists():
        raise SystemExit("No master resume yet. Run `python -m jobagent parse-resume path/to/resume.pdf`.")
    return Resume.model_validate_json(MASTER_RESUME_JSON.read_text(encoding="utf-8"))


TAILOR_SYSTEM = """You tailor a candidate's resume to a specific job description so it passes ATS
keyword screening and reads as a strong fit to a recruiter.

What you may do:
- Reorder sections, bullets and skills to lead with what the job cares about; rephrase bullets in the
  job's terminology where they describe the same work; tighten wording; drop or shorten content that is
  irrelevant to this job; rewrite the headline and summary for this role.
- Keep **bold** markers (double asterisks) on the key numbers and names, as in the master.
{skills_rule}

What you must not do:
- Never change or invent employers, job titles, dates, degrees, certifications, metrics or numbers,
  and never add bullets describing work that isn't in the master resume. Never inflate seniority.
- Keep company names, titles, dates, education and contact details exactly as in the master.

Length: one page - at most ~6 bullets for the most relevant role, fewer for less relevant ones.
Cover letter: only facts from the master resume (plus genuine interest in the added skills area),
address the company by name, no "[placeholder]" text, no clichés.

<master_resume>
{master}
</master_resume>"""

ADD_SKILLS_RULE = """- ADD SKILLS: you may add to the Skills section tools/technologies/concepts that the job asks for and
  that are missing from the master resume, when they are a credible fit for this candidate's background
  (e.g. adjacent tools in the same stack). Put them only in the skills lists - not in experience bullets,
  projects or the summary. List every such addition in `added_skills`. Don't add more than ~6."""
NO_NEW_SKILLS_RULE = """- Do not add any skill, tool or technology that is not already in the master resume.
  Leave `added_skills` empty."""


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")[:60]


def _unknown_skills(tailored: Resume, master: Resume) -> list[str]:
    """Skills that don't appear anywhere in the master resume - a fabrication guard."""
    haystack = master.model_dump_json().lower()
    return [s for g in tailored.skills for s in g.items if s.lower() not in haystack]


def tailor(job: dict, master: Resume) -> tuple[TailoredResume, Path, Path]:
    result = structured(
        TailoredResume,
        system=TAILOR_SYSTEM.format(
            master=master.model_dump_json(indent=1),
            skills_rule=ADD_SKILLS_RULE if cfg("llm.allow_added_skills", True) else NO_NEW_SKILLS_RULE,
        ),
        content=(
            f"Company: {job['company']}\nRole: {job['title']}\nLocation: {job['location']}\n\n"
            f"{untrusted('job_description', job['description'] or '')}\n\n"
            "Produce the tailored resume and cover letter."
        ),
        effort=cfg("llm.tailor_effort", "high"),
        task="tailor",
    )
    new = _unknown_skills(result.resume, master)
    if cfg("llm.allow_added_skills", True):
        # Make sure every skill that isn't in the master is reported, so you can review it.
        seen = {x.lower() for x in result.added_skills}
        result.added_skills += [x for x in new if x.lower() not in seen]
        if result.added_skills:
            result.changes.insert(0, "Added skills (not in your base resume - be ready to discuss them): "
                                  + ", ".join(result.added_skills))
    elif new:
        bad = {x.lower() for x in new}
        for g in result.resume.skills:
            g.items = [x for x in g.items if x.lower() not in bad]
        result.changes.append(f"Removed skills not found in master resume: {', '.join(new)}")
    # Contact details always come from the master.
    for f in ("name", "email", "phone", "location", "links"):
        setattr(result.resume, f, getattr(master, f))

    out = OUTPUT_DIR / f"{_slug(job['company'])}_{_slug(job['title'])}_{job['id'][:6]}"
    out.mkdir(parents=True, exist_ok=True)
    first = _slug(master.name.split()[0].title()) if master.name else "Resume"
    trimmed: list[str] = []
    pdf = render_pdf(result.resume, out / f"{first}_Resume_{_slug(job['company'])}.pdf", trimmed)
    if trimmed:
        result.changes.append("Trimmed to fit one page: removed " + "; ".join(trimmed))
    cover = out / "cover_letter.txt"
    cover.write_text(result.cover_letter, encoding="utf-8")
    render_cover_pdf(master, result.cover_letter, cover.with_suffix(".pdf"))
    (out / "resume.json").write_text(result.resume.model_dump_json(indent=2), encoding="utf-8")
    (out / "job.json").write_text(json.dumps(dict(job), indent=2, default=str), encoding="utf-8")
    return result, pdf, cover


def _md(text: str) -> Markup:
    """Escape, then turn **bold** markers into <b>; bold numbers/metrics get the accent colour
    (as in the original resume)."""
    def bold(m: re.Match) -> str:
        inner = m.group(1)
        cls = ' class="num"' if re.search(r"\d", inner) else ""
        return f"<b{cls}>{inner}</b>"

    return Markup(re.sub(r"\*\*(.+?)\*\*", bold, str(escape(text or ""))))


def render_html(resume: Resume) -> str:
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(default=True))
    env.filters["md"] = _md
    return env.get_template("resume.html.j2").render(r=resume)


def _html_to_pdf(markup: str, path: Path, margin_mm: int = 10, fit_one_page: bool = False) -> Path:
    m = f"{margin_mm}mm"
    with pdf_page() as page:
        page.set_content(markup, wait_until="networkidle")
        if fit_one_page:
            # Measure at the printable A4 width; shrink slightly (not below 85%) if it spills onto page 2.
            px_per_mm = 96 / 25.4
            page.set_viewport_size({"width": round((210 - 2 * margin_mm) * px_per_mm), "height": 1000})
            page.emulate_media(media="print")
            height = page.evaluate("document.body.scrollHeight")
            capacity = (297 - 2 * margin_mm) * px_per_mm - 4
            if height > capacity:
                page.evaluate(f"document.body.style.zoom = {max(0.85, capacity / height):.3f}")
        page.pdf(path=str(path), format="A4", print_background=True,
                 margin={"top": m, "bottom": m, "left": m, "right": m})
    return path


def _pages(path: Path) -> int:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(str(path))
    try:
        return len(doc)
    finally:
        doc.close()


def _trim_once(r: Resume) -> str | None:
    """Drop the least important piece of content; returns what was dropped, or None if nothing left to trim."""
    if len(r.projects) > 1:
        return f"project '{r.projects.pop().name}'"
    for p in r.projects:
        if len(p.bullets) > 1:
            p.bullets.pop()
            return f"a bullet from project '{p.name}'"
    for e in reversed(r.experience):  # older / less relevant roles first
        if len(e.bullets) > (4 if e is r.experience[0] else 1):
            e.bullets.pop()
            return f"a bullet from {e.company}"
    if r.projects:
        return f"project '{r.projects.pop().name}'"
    return None


def render_pdf(resume: Resume, path: Path, trimmed: list[str] | None = None) -> Path:
    """Render to a one-page PDF: scale down slightly if needed, then trim the least relevant content."""
    r = resume.model_copy(deep=True)
    _html_to_pdf(render_html(r), path, fit_one_page=True)
    while _pages(path) > 1:
        dropped = _trim_once(r)
        if not dropped:
            break
        if trimmed is not None:
            trimmed.append(dropped)
        _html_to_pdf(render_html(r), path, fit_one_page=True)
    return path


def render_cover_pdf(master: Resume, letter: str, path: Path) -> Path:
    env = Environment(autoescape=select_autoescape(default=True))
    markup = env.from_string(
        "<html><body style=\"font-family:Calibri,'Segoe UI',Arial;font-size:11pt;line-height:1.45\">"
        "<b style='font-size:14pt'>{{ m.name }}</b><br>{{ m.email }}{% if m.phone %} | {{ m.phone }}{% endif %}"
        "<br><br>{% for p in paras %}<p>{{ p }}</p>{% endfor %}</body></html>"
    ).render(m=master, paras=[p for p in letter.split("\n\n") if p.strip()])
    return _html_to_pdf(markup, path, margin_mm=20)
