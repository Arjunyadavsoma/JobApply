"""Quick fit estimate (0-100) for every job, computed locally - no LLM call.

Used to order jobs that haven't been scored yet, and to decide which jobs to score first.
  - skills (up to 50): how many of your resume's skills the job title/description mention
  - title  (up to 20): the title matches your search keywords / target roles
  - experience (up to 30): the years the job asks for fit your experience
then scaled down when the job asks for far more experience, is at the wrong level for the
CANDIDATE'S career stage, or is built around a stack the candidate doesn't have.

The level penalty is profile-aware: for a fresher/entry-level candidate, fresher/trainee/intern
titles are the TARGET (no penalty) while senior titles (Staff, Principal, ...) are penalised;
for an experienced candidate the reverse holds. The AI score (after "Score") is always the
real ranking; this is only a first pass.
"""
from __future__ import annotations

import re

from jobagent import db
from jobagent.config import cfg
from jobagent.models import Resume

GENERIC = {"engineer", "developer", "software", "senior", "junior", "lead", "the", "and", "for", "with"}
# Common primary stacks. Ones that aren't on your resume count against a job that centres on them.
STACKS = ["java", "python", "go", "golang", "c#", ".net", "dotnet", "php", "ruby", "rails", "node.js", "nodejs",
          "node", "typescript", "javascript", "kotlin", "swift", "scala", "rust", "c++", "elixir", "sap", "abap",
          "salesforce", "android", "ios", "flutter", "react native", "angular", "vue", "next.js", "django",
          "flask", "fastapi", "laravel", "spring boot", "react", "mainframe", "cobol"]
# Levels that don't fit a candidate with ~2-3 years of experience (the historical default).
SENIOR_LEVELS = ["staff", "principal", "architect", "director", "head of", "vp", "vice president", "lead",
                 "senior", "sr.", "manager", "sde-3", "sde3"]
# Entry-level signals: wrong level for an experienced candidate, RIGHT level for a fresher.
ENTRY_LEVELS = ["fresher", "trainee", "intern", "internship", "graduate", "apprentice", "entry level",
                "entry-level", "graduate engineer"]
STRONG_ASK = re.compile(r"[^.\n]{0,60}(years|yrs|experience|expert|strong|proficien)")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower()).strip()


def _pattern(term: str) -> str:
    return r"(?<![a-z0-9+#])" + re.escape(term) + r"(?![a-z0-9+#])"


def _has(text: str, term: str) -> bool:
    return re.search(_pattern(term), text) is not None


def resume_skills(r: Resume) -> list[str]:
    """Concrete technical terms from the resume (not soft 'competencies')."""
    terms: list[str] = []
    for g in r.skills:
        if g.category.lower() == "core competencies":
            continue
        terms += g.items
    for p in r.projects:
        terms += p.tech
    out, seen = [], set()
    for t in terms:
        for part in re.split(r"\s*/\s*|\s*\(|\)", t):  # "PostgreSQL / MySQL", "AWS (EC2)" -> parts
            n = _norm(part)
            if 1 < len(n) <= 30 and n not in seen and n not in GENERIC:
                seen.add(n)
                out.append(n)
    return out


def _candidate_stage() -> str:
    """'fresher' | 'experienced' - from the candidate profile (or applicant config)."""
    try:
        from jobagent.profile import load as load_profile
        p = load_profile()
        if p.candidate_type == "experienced" or (p.total_experience_years or 0) >= 3:
            return "experienced"
        return "fresher"
    except Exception:
        years = _your_years()
        return "experienced" if years and years >= 3 else "fresher"


def _wrong_levels(stage: str) -> list[str]:
    """Titles at the wrong level for THIS candidate: seniors for a fresher, entry for a veteran."""
    return ENTRY_LEVELS if stage == "experienced" else SENIOR_LEVELS


def _required_years(text: str) -> tuple[float, float] | None:
    """Smallest experience requirement mentioned: '2-5 years', '3+ yrs', 'minimum 4 years'."""
    best = None
    for m in re.finditer(r"(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})\s*\+?\s*(?:years|yrs)", text):
        lo, hi = float(m.group(1)), float(m.group(2))
        if lo <= hi and (best is None or lo < best[0]):
            best = (lo, hi)
    if best is None:
        for m in re.finditer(r"(\d{1,2})\s*\+?\s*(?:years|yrs)", text):
            lo = float(m.group(1))
            if lo <= 20 and (best is None or lo < best[0]):
                best = (lo, lo + 3)
    return best


def _your_years() -> float | None:
    m = re.search(r"\d+(\.\d+)?", str(cfg("applicant.total_experience_years", "")))
    return float(m.group()) if m else None


def estimate(job, skills: list[str], keywords: list[str], years: float | None) -> int:
    title = _norm(job["title"] or "")
    text = _norm(f"{job['title'] or ''} {job['description'] or ''}")

    hits = sum(1 for s in skills if _has(text, s))
    skill_pts = min(50, hits * 6)  # ~8 of your skills mentioned = full marks

    words = {w for k in keywords for w in _norm(k).split() if w not in GENERIC}
    title_pts = 20 if any(_has(title, w) for w in words) else 12 if any(
        _has(title, w) for w in ("engineer", "developer", "sde", "programmer")) else 0

    exp_pts, factor = 15, 1.0  # unknown requirement: neutral
    req = _required_years(text)
    if req and years is not None:
        lo, hi = req
        if lo <= years <= hi + 1:
            exp_pts = 30
        elif lo <= years + 1:
            exp_pts = 20
        elif lo <= years + 2:
            exp_pts, factor = 8, 0.8
        else:
            exp_pts, factor = 0, 0.45  # asks for far more experience - rarely worth it

    if any(_has(title, w) for w in _wrong_levels(_candidate_stage())):
        factor = min(factor, 0.5)

    yours = set(skills)
    foreign = [t for t in STACKS if not any(t == y or t in y.split() for y in yours)]
    if any(_has(title, t) for t in foreign):
        factor = min(factor, 0.5)  # e.g. "C# Full-Stack Developer", "SAP ...", "Go Backend Engineer"
    else:
        # Description built around other stacks: those asked for with years / "strong" / "expert".
        heavy = sum(1 for t in foreign if re.search(_pattern(t) + STRONG_ASK.pattern, text))
        factor *= max(0.6, 1 - 0.15 * heavy)

    return int(round(min(100, skill_pts + title_pts + exp_pts) * factor))


def update(all_jobs: bool = False) -> int:
    """Compute the estimate for jobs that don't have one (or for every job). Returns how many."""
    from jobagent.config import MASTER_RESUME_JSON
    from jobagent.resume import load_master

    if not MASTER_RESUME_JSON.exists():
        return 0
    skills = resume_skills(load_master())
    keywords = cfg("search.keywords", []) or []
    years = _your_years()
    with db.connect() as conn:
        where = "" if all_jobs else "WHERE prefit IS NULL"
        rows = conn.execute(f"SELECT id, title, description FROM jobs {where}").fetchall()
        conn.executemany("UPDATE jobs SET prefit = ? WHERE id = ?",
                         [(estimate(r, skills, keywords, years), r["id"]) for r in rows])
    return len(rows)
