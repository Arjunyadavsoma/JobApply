"""Human-in-the-loop applying.

For each tailored job the agent opens the application page in a visible browser, reads every form
field, asks Claude to answer them from your resume + `applicant:` facts, fills them in, uploads the
tailored resume, and highlights anything it couldn't answer. **You** review and press Submit;
the agent never clicks submit itself.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import Error as PWError, Frame, Page
from rich.console import Console
from rich.prompt import Prompt

from jobagent import db
from jobagent.browser import browser_context
from jobagent.config import DATA_DIR, cfg
from jobagent.llm import LLMError, structured, untrusted
from jobagent.models import FormAnswers, Resume

console = Console()

SCREENSHOT_DIR = DATA_DIR / "screenshots"

# Collects every fillable control in a frame, tags it with data-ja-id and returns a description.
EXTRACT_JS = r"""
(args) => {
  const prefix = args.prefix;
  const scope = args.root ? document.querySelector(args.root) : document;
  if (!scope) return [];
  const clean = s => (s || '').replace(/\s+/g, ' ').trim().slice(0, 300);
  const visible = el => {
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const labelOf = el => {
    let t = '';
    if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) t = l.innerText; }
    if (!t && el.getAttribute('aria-labelledby'))
      t = el.getAttribute('aria-labelledby').split(/\s+/).map(i => document.getElementById(i)?.innerText || '').join(' ');
    if (!t) t = el.getAttribute('aria-label') || '';
    if (!t) { const l = el.closest('label'); if (l) t = l.innerText; }
    if (!t) {
      let p = el.parentElement;
      for (let i = 0; i < 4 && p && !t; i++, p = p.parentElement) {
        const l = p.querySelector('label, legend, [class*="label"], [class*="question"]');
        if (l && !l.contains(el)) t = l.innerText;
      }
    }
    return clean(t || el.placeholder || el.name);
  };
  const groupQuestion = el => {
    const fs = el.closest('fieldset');
    if (fs) { const lg = fs.querySelector('legend'); if (lg) return clean(lg.innerText); }
    let p = el.parentElement;
    for (let i = 0; i < 5 && p; i++, p = p.parentElement) {
      const l = p.querySelector('legend, [class*="label"], [class*="question"], label:not(:has(input))');
      if (l && !l.contains(el)) return clean(l.innerText);
    }
    return clean(el.name);
  };
  // Custom dropdowns keep the chosen option in a sibling element, not in the input's value.
  const comboValue = el => {
    let p = el.parentElement;
    for (let i = 0; i < 6 && p; i++, p = p.parentElement) {
      const chosen = p.querySelectorAll(
        '[class*="single-value"], [class*="singleValue"], [class*="multi-value__label"], [class*="multiValue"] > div:first-child');
      if (chosen.length) return clean([...chosen].map(c => c.innerText).join('; '));
      const cls = (p.className && p.className.toString()) || '';
      if (/(^|[\s_-])control/i.test(cls)) {   // reached the dropdown box without finding a selection
        const t = clean(p.innerText);
        return /^(select\.*|choose\.*|start typing\.*|search\.*|)$/i.test(t) ? '' : t;
      }
    }
    return clean(el.value);
  };
  const selectValue = el => {
    const o = el.selectedIndex >= 0 ? el.options[el.selectedIndex] : null;
    const t = o ? clean(o.text) : '';
    return !o || o.value === '' || /^(select|choose|please select|--|—|none selected)/i.test(t) ? '' : t;
  };
  const required = el => el.required || el.getAttribute('aria-required') === 'true';

  const out = [], groups = {};
  let n = 0;
  for (const el of scope.querySelectorAll('input, textarea, select')) {
    const tag = el.tagName.toLowerCase();
    const type = tag === 'select' ? 'select' : tag === 'textarea' ? 'textarea' : (el.type || 'text').toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset', 'search', 'password'].includes(type)) continue;
    if (el.disabled || el.readOnly) continue;
    if (type !== 'file' && type !== 'radio' && type !== 'checkbox' && !visible(el)) continue;
    // Shadow inputs that custom dropdowns (react-select etc.) use for validation.
    if (type !== 'file' && (el.getAttribute('aria-hidden') === 'true' ||
        (el.tabIndex === -1 && parseFloat(getComputedStyle(el).opacity) === 0))) continue;

    if ((type === 'radio' || type === 'checkbox') && el.name &&
        document.querySelectorAll(`input[name="${CSS.escape(el.name)}"]`).length > 1) {
      const key = type + ':' + el.name;
      if (!groups[key]) {
        groups[key] = { id: prefix + (n++), kind: type === 'radio' ? 'radio' : 'checkbox_group',
                        label: groupQuestion(el), name: el.name, required: required(el), options: [], value: '' };
        out.push(groups[key]);
      }
      const opt = labelOf(el) || el.value;
      el.setAttribute('data-ja-id', groups[key].id);
      el.setAttribute('data-ja-opt', opt);
      groups[key].options.push(opt);
      if (el.checked) groups[key].value += (groups[key].value ? '; ' : '') + opt;
      continue;
    }
    const id = prefix + (n++);
    el.setAttribute('data-ja-id', id);
    const kind = type === 'checkbox' ? 'checkbox'
               : el.getAttribute('role') === 'combobox' ? 'combobox' : type;
    const f = { id, kind, label: labelOf(el), name: el.name || el.id || '', required: required(el),
                value: type === 'checkbox' ? String(el.checked)
                       : type === 'file' ? (el.files && el.files.length ? 'uploaded' : '')
                       : kind === 'combobox' ? comboValue(el)
                       : tag === 'select' ? selectValue(el) : clean(el.value) };
    if (el.placeholder && el.placeholder !== f.label) f.placeholder = clean(el.placeholder);
    if (el.maxLength > 0 && el.maxLength < 100000) f.max_length = el.maxLength;
    if (tag === 'select') f.options = [...el.options].map(o => clean(o.text)).filter(Boolean);
    if (type === 'file') f.accept = el.accept || '';
    out.push(f);
  }
  return out;
}
"""


@dataclass
class FormField:
    frame: Frame
    meta: dict

    @property
    def locator(self):
        return self.frame.locator(f'[data-ja-id="{self.meta["id"]}"]')

    @property
    def key(self) -> tuple[str, str]:
        """Stable identity across re-scans (data-ja-id numbering can shift when fields appear)."""
        return self.meta["label"], self.meta.get("name", "")


def _combobox_options(field: FormField, limit: int = 60) -> list[str]:
    """Custom dropdowns only render their options when opened: open, read, close."""
    loc = field.locator.first
    try:
        loc.click(timeout=3000)
        field.frame.page.wait_for_timeout(500)
        texts = [t.strip() for t in field.frame.locator('[role="option"]:visible').all_inner_texts()[:limit]]
        loc.press("Escape")
        return [t for t in texts if t]
    except PWError:
        return []


def extract_fields(page: Page, read_options: bool = True, root: str | None = None) -> dict[str, FormField]:
    fields: dict[str, FormField] = {}
    for i, frame in enumerate(page.frames):
        try:
            for meta in frame.evaluate(EXTRACT_JS, {"prefix": f"fr{i}_", "root": root}):
                fields[meta["id"]] = FormField(frame, meta)
        except PWError:
            continue  # cross-origin or detached frame
    if read_options:
        for f in fields.values():
            if f.meta["kind"] == "combobox" and not f.meta.get("value"):
                opts = _combobox_options(f)
                if opts:
                    f.meta["options"] = opts
                    if len(opts) >= 60:
                        f.meta["note"] = "long searchable list; only the first options shown - give the exact answer"
                else:
                    f.meta["note"] = "searchable dropdown; options load as you type - give the exact text to type"
    return fields


def _is_empty(f: FormField) -> bool:
    v = (f.meta.get("value") or "").strip().lower()
    return v != "true" if f.meta["kind"] == "checkbox" else v == ""


FORM_SYSTEM = """You fill in job application forms on the candidate's behalf. The candidate reviews every
form in the browser before submitting it, so aim to fill EVERY field you can answer correctly.

How to answer:
- Use the candidate facts, the full resume and the cover letter below. You MAY derive answers that follow
  directly from them: first/last name from the full name; city/state/country from the location; total or
  per-skill years of experience from the resume dates (today is {today}); graduation year, degree, school
  and GPA from education; current employer and title; "have you worked at <company> before?" = No unless
  that company appears in the resume; phone with/without the +91 country code as the field expects;
  whether the candidate has a skill = yes only if the resume shows it.
- Never invent anything else: no made-up salaries, notice periods, dates of birth, addresses, IDs,
  references, or experience the resume doesn't show. If a REQUIRED field can't be answered this way,
  list its field_id in `needs_human`. Skip unanswerable optional fields.
- Screening yes/no questions: answer truthfully even when the answer is "No"
  (e.g. "5+ years of Java?" -> No if the resume shows 2).
- Numbers: plain digits in the unit the field asks for (e.g. years "2"; CTC "12" if the label says LPA).
- Skip fields whose current `value` is already correct; correct values that are wrong.
- select / radio / combobox with `options`: `value` must be exactly one of the listed options.
  Comboboxes without options: give the exact text to type (e.g. "Hyderabad" or "India").
  checkbox_group: option labels separated by "; ". checkbox: "true" or "false".
- File inputs: "resume" for the resume/CV field, "cover_letter" for a cover-letter field. Skip other files.
- Cover-letter or "additional information" text areas: use the cover letter text (respect max_length).
- Free-text questions ("Why do you want to work here?", "Describe a project..."): 2-5 sentences in the
  candidate's own voice, specific to this job, using only facts from the resume. Respect max_length.
- Legal consents, attestations, terms/privacy agreements, background-check authorisations and signatures:
  do NOT answer - list them in `needs_human` so the candidate agrees personally.
- Voluntary demographic (EEO) questions: use the facts if given; otherwise choose the "decline to
  self-identify"-style option if there is one; otherwise skip.

<candidate_facts>
{facts}
</candidate_facts>

<resume>
{resume}
</resume>"""


def ask_claude(fields: list[FormField], job, resume: Resume, cover_letter: str,
               already: list[FormField]) -> FormAnswers:
    from datetime import date

    from jobagent.profile import facts_for_forms
    facts = "\n".join(f"- {k}: {v}" for k, v in facts_for_forms().items())
    context = [{"label": f.meta["label"], "value": f.meta["value"]} for f in already if not _is_empty(f)]
    return structured(
        FormAnswers,
        system=FORM_SYSTEM.format(facts=facts, resume=resume.model_dump_json(indent=1),
                                  today=date.today().isoformat()),
        content=(
            f"Job: {job['title']} at {job['company']} ({job['location'] or 'location n/a'})\n\n"
            f"{untrusted('job_description', job['description'] or '')}\n\n"
            f"<cover_letter>\n{cover_letter}\n</cover_letter>\n\n"
            f"<already_filled>\n{json.dumps(context, indent=1)}\n</already_filled>\n\n"
            f"<form_fields_to_fill>\n{json.dumps([f.meta for f in fields], indent=1)}\n</form_fields_to_fill>"
        ),
        effort=cfg("llm.form_effort", "medium"),
        task="form",
    )


def _best_option(options: list[str], value: str) -> str | None:
    v = value.strip().lower()
    tests = (lambda o: o.lower() == v, lambda o: o.lower().startswith(v), lambda o: v in o.lower(),
             lambda o: o.lower() in v)
    for test in tests:
        hit = next((o for o in options if o.strip() and test(o.strip())), None)
        if hit:
            return hit
    return None


def _pick_combobox(field: FormField, value: str) -> None:
    loc, page = field.locator.first, field.frame.page
    loc.click()
    loc.fill(value)
    for _ in range(6):  # options may load asynchronously (city / school search)
        page.wait_for_timeout(500)
        opts = field.frame.locator('[role="option"]:visible')
        texts = [t.strip() for t in opts.all_inner_texts()]
        best = _best_option(texts, value)
        if best:
            opts.nth(texts.index(best)).click()
            return
    loc.press("Enter")


def _fill_one(field: FormField, value: str, files: dict[str, Path]) -> None:
    meta, loc = field.meta, field.locator
    kind = meta["kind"]
    if kind == "file":
        path = files.get(value.strip().lower())
        if path and path.exists():
            loc.first.set_input_files(str(path))
    elif kind == "select":
        loc.first.select_option(label=_best_option(meta.get("options", []), value) or value)
    elif kind in ("radio", "checkbox_group"):
        for w in (x.strip() for x in value.split(";") if x.strip()):
            opt = _best_option(meta.get("options", []), w)
            for i in range(loc.count()):
                if opt and (loc.nth(i).get_attribute("data-ja-opt") or "") == opt:
                    loc.nth(i).check(force=True)
    elif kind == "checkbox":
        loc.first.set_checked(value.strip().lower() == "true", force=True)
    elif kind == "combobox":
        _pick_combobox(field, value)
    else:
        loc.first.fill(value)


def _flag(field: FormField, color: str) -> None:
    try:
        field.locator.first.evaluate(
            f"el => {{ el.style.outline = '3px solid {color}'; el.style.outlineOffset = '2px'; }}"
        )
    except PWError:
        pass


def _upload_files(fields: dict[str, FormField], files: dict[str, Path]) -> int:
    """Upload resume / cover letter first: many ATSs parse the resume and pre-fill other fields."""
    done = 0
    for f in fields.values():
        if f.meta["kind"] != "file" or f.meta.get("value"):
            continue
        hint = f"{f.meta['label']} {f.meta.get('name', '')}".lower()
        key = "cover_letter" if "cover" in hint else "resume" if ("resume" in hint or "cv" in hint) else None
        if key and files.get(key) and files[key].exists():
            try:
                f.locator.first.set_input_files(str(files[key]))
                done += 1
            except PWError:
                pass
    return done


TEXTLIKE = ("text", "textarea", "email", "tel", "url", "number", "date")


def _screenshot(page, job_id: str, tag: str) -> str | None:
    """Debug evidence for every fill step; failure to screenshot never blocks the flow."""
    try:
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        path = SCREENSHOT_DIR / f"{job_id}_{tag}.png"
        page.screenshot(path=str(path), full_page=True)
        return str(path)
    except PWError:
        return None


def _fraud_guard(job) -> list[str]:
    """Refuse to auto-fill when trust warnings are unresolved (payment asks, odd domains)."""
    import json as _json
    try:
        flags = _json.loads(job["fraud_flags"] or "[]")
    except ValueError:
        flags = []
    return [f for f in flags if isinstance(f, str)]


def autofill(page: Page, job, resume: Resume, cover_letter: str, files: dict[str, Path], passes: int = 3,
             root: str | None = None) -> None:
    """Fill the current page: upload files, then up to `passes` rounds, since answering
    one question (e.g. country) often reveals new ones (e.g. state)."""
    from jobagent.resume import load_master

    flags = _fraud_guard(job)
    if flags:
        console.print("[red]Trust warnings on this job - NOT auto-filling:[/red]")
        for f in flags:
            console.print(f"  - {f}")
        console.print("Verify the employer and use the original job link manually if it is genuine.")
        return

    fields = extract_fields(page, read_options=False, root=root)
    if not fields and root:
        console.print("Nothing to fill on this step.")
        return
    if not fields:
        console.print("[yellow]No form fields found on this page. Open the application form "
                      "(e.g. click Apply / Easy Apply), then fill again.[/yellow]")
        return
    uploaded = _upload_files(fields, files)
    if uploaded:
        console.print(f"Uploaded {uploaded} file(s); waiting for the site to read them...")
        page.wait_for_timeout(4000)

    master = load_master()  # the full, untrimmed resume is the fact source for answers
    attempted: set[tuple[str, str]] = set()
    needs_human: set[tuple[str, str]] = set()
    failed: dict[tuple[str, str], str] = {}
    filled = 0

    for round_no in range(1, passes + 1):
        fields = extract_fields(page, root=root)
        # Empty fields, plus pre-filled text fields on the first pass (the site's own resume parser
        # sometimes gets them wrong) - each field is sent to Claude at most once.
        todo = [f for f in fields.values() if f.key not in attempted and not
                (f.meta["kind"] == "file" and f.meta.get("value")) and
                (_is_empty(f) or (round_no == 1 and f.meta["kind"] in TEXTLIKE))]
        if not todo:
            break
        console.print(f"Pass {round_no}: {len(todo)} fields - asking Claude...")
        try:
            answers = ask_claude(todo, job, master, cover_letter, [f for f in fields.values() if f not in todo])
        except LLMError as e:
            console.print(f"[red]{e}[/red]")
            return
        by_id = {f.meta["id"]: f for f in todo}
        attempted |= {f.key for f in todo}
        for a in answers.answers:
            f = by_id.get(a.field_id)
            if not f or not a.value.strip() or (f.meta.get("value") or "").strip() == a.value.strip():
                continue
            try:
                _fill_one(f, a.value, files)
                filled += 1
                failed.pop(f.key, None)
            except PWError as e:
                failed[f.key] = f"{f.meta['label']} ({str(e).splitlines()[0][:80]})"
        needs_human |= {by_id[i].key for i in answers.needs_human if i in by_id}
        page.wait_for_timeout(1200)  # let dependent fields render before the next pass

    # Verify: re-read the page and flag required fields that are still empty.
    final = extract_fields(page, read_options=False, root=root)
    red: list[str] = []
    for f in final.values():
        if f.key in needs_human or (f.meta.get("required") and _is_empty(f) and f.key not in failed):
            _flag(f, "red")
            red.append(f.meta["label"])
        elif f.key in failed:
            _flag(f, "orange")

    have = sum(1 for f in final.values() if not _is_empty(f))
    console.print(f"[green]Filled {filled} fields - {have} of {len(final)} fields on the page now have a value.[/green]")
    if red:
        console.print("[red]Needs you (outlined red):[/red] " + "; ".join(dict.fromkeys(red)))
    if failed:
        console.print("[yellow]Couldn't fill (outlined orange):[/yellow] " + "; ".join(failed.values()))
    tag = (page.url or "page").split("/")[-1][:12].replace(".", "") or "page"
    _screenshot(page, job["id"], f"filled_{tag}")
    db.record_event(job["id"], "apply", f"Auto-filled {filled} fields ({len(red)} need you)")


def _job_materials(job) -> tuple[Resume, str, dict[str, Path]]:
    """Tailored resume + cover letter if the job has them, otherwise your original resume PDF."""
    if not job["resume_path"] or not Path(job["resume_path"]).exists():
        from jobagent.config import BASE_RESUME_PDF
        from jobagent.resume import load_master

        return load_master(), "", {"resume": BASE_RESUME_PDF}
    resume_path = Path(job["resume_path"])
    cover_txt = Path(job["cover_path"])
    cover_letter = cover_txt.read_text(encoding="utf-8") if cover_txt.exists() else ""
    tailored = Resume.model_validate_json((resume_path.parent / "resume.json").read_text(encoding="utf-8"))
    return tailored, cover_letter, {"resume": resume_path, "cover_letter": cover_txt.with_suffix(".pdf")}


def run_one(job_id: str, tailor_first: bool = False) -> None:
    """Dashboard-driven session: open one job, auto-fill, then wait for commands.

    The dashboard writes "fill" (re-scan and fill the current page / next step) or "close" to
    data/apply_control.txt. The session also ends when you close the browser window.
    """
    import time

    from jobagent.tasks import CONTROL

    job = db.get(job_id)
    if not job:
        console.print("Job not found.")
        return
    if tailor_first:
        from jobagent.cli import tailor_job

        if not tailor_job(job_id):
            return
        job = db.get(job_id)
    tailored, cover_letter, files = _job_materials(job)
    console.print("Using your " + ("tailored resume + cover letter" if cover_letter else "original resume (not tailored)"))
    CONTROL.unlink(missing_ok=True)

    with browser_context() as ctx:
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        from jobagent import linkedin_apply

        console.print(f"Opening {job['company']} - {job['title']}\n{job['apply_url']}")
        if linkedin_apply.is_linkedin(job["apply_url"] or job["url"]):
            try:
                page = linkedin_apply.apply(page, job, tailored, cover_letter, files)
            except PWError as e:
                console.print(f"[red]LinkedIn step failed: {str(e).splitlines()[0]}[/red] - continue in Chrome, "
                              "then press 'Fill current page'.")
        else:
            try:
                page.goto(job["apply_url"], wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)
            except PWError as e:
                console.print(f"Could not open page: {e}")
            one_click = next((name for site, name in (("naukri.com", "Naukri"), ("instahyre.com", "Instahyre"))
                              if site in page.url), None)
            if one_click:
                # Naukri's and Instahyre's Apply buttons submit instantly with your profile on that site,
                # so they are never auto-clicked.
                console.print(f"{one_click}: log in if needed and click Apply yourself (it applies instantly with "
                              f"your {one_click} profile). If it opens a questionnaire or a company site, press "
                              "'Fill current page'.")
            else:
                autofill(page, job, tailored, cover_letter, files)
        console.print("\nReview the form in the browser and click Submit yourself. Then press "
                      "'Mark applied' in the dashboard. Close the browser window when done.")

        while True:
            try:
                page.wait_for_timeout(1000)   # keeps Playwright responsive; raises once closed
            except PWError:
                break
            if not ctx.pages:
                break
            page = ctx.pages[-1]              # follow the newest tab (e.g. apply opens a new one)
            if CONTROL.exists():
                cmd = CONTROL.read_text(encoding="utf-8").strip()
                CONTROL.unlink(missing_ok=True)
                if cmd == "fill":
                    console.rule("Filling current page")
                    try:
                        linkedin_apply.fill_current(page, job, tailored, cover_letter, files)
                    except PWError as e:
                        console.print(f"[red]{str(e).splitlines()[0]}[/red]")
                elif cmd == "close":
                    break
            time.sleep(0.2)
    console.print("Browser closed.")


def run(limit: int | None = None) -> None:
    from jobagent.resume import load_master

    master = load_master()
    jobs = db.by_status("tailored")[:limit]
    if not jobs:
        console.print("No tailored jobs waiting. Run `python -m jobagent tailor` first.")
        return

    with browser_context() as ctx:
        for n, job in enumerate(jobs, 1):
            tailored, cover_letter, files = _job_materials(job)
            resume_path = files["resume"]

            console.rule(f"[{n}/{len(jobs)}] {job['company']} - {job['title']}  (score {job['score']})")
            console.print(f"{job['apply_url']}\nResume: {resume_path}")
            page = ctx.new_page()
            try:
                page.goto(job["apply_url"], wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)
            except PWError as e:
                console.print(f"[red]Could not open page: {e}[/red]")

            if any(site in page.url for site in ("linkedin.com", "naukri.com")):
                console.print("[cyan]Log in if needed, click Apply / Easy Apply, then press f to fill each step.[/cyan]")
            else:
                autofill(page, job, tailored, cover_letter, files)

            while True:
                choice = Prompt.ask(
                    "[b]f[/b]=fill (again/next step)  [b]a[/b]=I submitted it  [b]s[/b]=skip for now  "
                    "[b]d[/b]=dismiss job  [b]q[/b]=quit",
                    choices=["f", "a", "s", "d", "q"], default="s",
                )
                if choice == "f":
                    autofill(page, job, tailored, cover_letter, files)
                    continue
                if choice == "a":
                    db.update(job["id"], status="applied", applied_at=db.now())
                    console.print("[green]Marked as applied.[/green]")
                elif choice == "d":
                    db.update(job["id"], status="dismissed")
                break
            page.close()
            if choice == "q":
                break
