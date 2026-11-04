"""Deterministic job-posting enrichment - no LLM, pure text parsing.

Extracts India-first facts from a job's title + description so that filtering, eligibility and
analytics work on structured data instead of raw text:
  - salary: CTC in LPA ("₹3 LPA", "3-5 LPA", "Rs 4.5 Lakhs"), monthly stipend for internships
  - experience range ("0-2 years", "fresher", "3+ yrs")
  - employment type (internship / full-time / part-time / contract / apprentice / trainee)
  - remote / hybrid / onsite
  - eligible graduation batches ("2026, 2027 and 2028 batch", "batch of 2027")
  - CGPA / percentage cutoffs ("6.5 CGPA", "60% throughout academics")
  - backlog policies ("no active backlogs")
  - application deadlines ("last date to apply: 20 Jan 2027")
  - fresher / walk-in / off-campus drive flags
  - fraud signals (registration fees, payment requests, free-mail apply links)

Everything is conservative: when in doubt, fields stay empty and eligibility.py says
"needs_verification" instead of guessing.
"""
from __future__ import annotations

import re
from datetime import date, datetime

from jobagent.models import JobFacts

RUPEE = r"(?:₹|rs\.?|inr)\s*"
RUPEE_OPT = r"(?:(?:₹|rs\.?|inr)\s*)?"   # properly optional prefix
NUM = r"(\d{1,3}(?:\.\d+)?)"


# ---------------------------------------------------------------- salary

def parse_salary(text: str) -> tuple[float | None, float | None, str, int | None]:
    """Returns (min_lpa, max_lpa, salary_text, stipend_inr_monthly).

    CTC / LPA is NEVER interpreted as take-home pay; stipends are kept separate.
    """
    t = re.sub(r"\s+", " ", text or "")
    low = high = None
    raw = ""

    # Range first: "₹3 - ₹5 LPA", "3-5 LPA", "3 to 5 LPA CTC", "Rs. 4.5 - 6 Lakh per annum"
    m = re.search(rf"{RUPEE_OPT}{NUM}\s*(?:-|–|to)\s*{RUPEE_OPT}{NUM}\s*(?:lpa|lakh|\u20b9l|l)\b\.?(?:\s*(?:pa|p\.a\.|per annum|ctc))?",
                  t, re.IGNORECASE)
    if m:
        low, high = float(m.group(1)), float(m.group(2))
        raw = m.group(0).strip()
    else:
        # Single value: "₹6 LPA", "12 LPA", "CTC: Rs 4.5 Lakhs", "Salary: 3.36 LPA"
        m = re.search(rf"(?:ctc|salary|package)[^\d]{{0,12}}{RUPEE_OPT}{NUM}\s*(?:lpa|lakhs?|l)\b",
                      t, re.IGNORECASE) or \
            re.search(rf"{RUPEE}{NUM}\s*(?:lpa|lakhs?|l)\b", t, re.IGNORECASE) or \
            re.search(rf"{NUM}\s*(?:lpa|lakhs?|l)\b\.?(?:\s*(?:pa|p\.a\.|per annum))?", t, re.IGNORECASE)
        if m and not re.search(rf"{NUM}\s*(?:-|–|to)\s*", m.group(0)):
            low = high = float(m.group(1))
            raw = m.group(0).strip()
    if low is not None and (low > 200 or (high or 0) > 200):   # 200+ "LPA" is almost certainly not salary
        low = high = None
        raw = ""

    # Monthly stipend: "stipend of ₹15,000/month", "internship stipend: 20000 per month"
    stipend = None
    m = re.search(rf"stipend[^.\n]{{0,50}}?{RUPEE_OPT}(\d[\d,]{{3,7}})\s*(?:/-|per\s*month|/month|monthly|\b)",
                  t, re.IGNORECASE)
    if m:
        stipend = int(m.group(1).replace(",", ""))
        if not (1000 <= stipend <= 500000):    # implausible monthly stipend
            stipend = None
        if stipend and low is None:      # internship with only a stipend: no LPA claim
            raw = raw or f"₹{stipend}/month stipend"
    return low, high, raw, stipend


# ---------------------------------------------------------------- experience

def parse_experience(text: str, employment_type: str) -> tuple[float | None, float | None]:
    """(min_years, max_years). Fresher/internship roles with no stated bar are (0, None)."""
    t = re.sub(r"\s+", " ", text or "")
    m = re.search(r"(\d{1,2})\s*(?:-|–|to)\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)", t, re.IGNORECASE)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"(\d{1,2})\s*\+?\s*(?:years?|yrs?)", t, re.IGNORECASE)
    if m and float(m.group(1)) <= 20:
        v = float(m.group(1))
        return v, None if v == 0 else v + 3
    if employment_type in ("internship", "trainee", "apprentice") or \
       re.search(r"\b(?:fresher|fresh graduate|entry[- ]level|no experience|0\s*years?|recent graduate)\b",
                 t, re.IGNORECASE):
        return 0.0, None
    return None, None


# ---------------------------------------------------------------- employment type / remote

def parse_employment_type(title: str, text: str) -> str:
    t = f"{title} {text[:400]}".lower()
    if re.search(r"\bintern(?:ship)?\b", t):
        return "internship"
    if re.search(r"\bapprentice(?:ship)?\b", t):
        return "apprentice"
    if re.search(r"\b(?:graduate engineer trainee|management trainee|graduate trainee|trainee engineer|software engineer trainee|get\b)", t):
        return "trainee"
    if re.search(r"\bpart[- ]time\b", t):
        return "part_time"
    if re.search(r"\bcontract(?:ual)?\b|\bcontractor\b", t):
        return "contract"
    return "full_time"


def parse_remote_type(title: str, text: str) -> str:
    t = f"{title} {text[:600]}".lower()
    if re.search(r"\b(?:work from home|wfh|fully remote|100% remote|remote[- ]first|remote first)\b", t):
        return "remote"
    if re.search(r"\bremote\b", t):
        # "remote" plus "hybrid" both mentioned -> hybrid-friendly; single mention -> remote
        return "hybrid" if re.search(r"\bhybrid\b", t) else "remote"
    if re.search(r"\bhybrid\b", t):
        return "hybrid"
    return "onsite" if t.strip() else ""


# ---------------------------------------------------------------- batches, academics

def parse_batches(text: str) -> list[int]:
    """Eligible graduation years, e.g. '2026, 2027, 2028 batch' or 'batch of 2025-2027'."""
    t = re.sub(r"\s+", " ", text or "")
    years: set[int] = set()
    # years first, then the word 'batch(es)': "2026, 2027 and 2028 batch"
    for m in re.finditer(r"((?:20\d{2}[\s,/&]*(?:and)?[\s,/&]*)+20\d{2})\s*(?:eligible\s+)?batch(?:es)?\b",
                         t, re.IGNORECASE):
        years |= {int(y) for y in re.findall(r"\b(20\d{2})\b", m.group(1))}
    # 'batch(es) of/from: 2026, 2027'
    for m in re.finditer(r"batch(?:es)?\s*(?:of|from)?[:\s]*((?:\d{4}[,\s/&]*(?:and)?[,\s/]*)+\d{4})", t, re.IGNORECASE):
        years |= {int(y) for y in re.findall(r"\b(20\d{2})\b", m.group(1))}
    for m in re.finditer(r"batch\s*(?:of|:)?\s*(20\d{2})", t, re.IGNORECASE):
        years.add(int(m.group(1)))
    # single year before the word: "2027 batch", "for 2027 batches"
    for m in re.finditer(r"\b(20\d{2})\s+(?:eligible\s+)?batch(?:es)?\b", t, re.IGNORECASE):
        years.add(int(m.group(1)))
    for m in re.finditer(r"\b(20\d{2})\s*(?:-|–|to)\s*(20\d{2})\s*batch", t, re.IGNORECASE):
        a, b = int(m.group(1)), int(m.group(2))
        years |= set(range(a, b + 1))
    # "2027 graduates may apply"
    for m in re.finditer(r"\b(20\d{2})\s*(?:passouts?|graduates?|passed\s*out)\b", t, re.IGNORECASE):
        years.add(int(m.group(1)))
    return sorted(y for y in years if 2000 <= y <= 2035)


def parse_academics(text: str) -> tuple[float | None, float | None, str]:
    """(min_cgpa/10, min_percentage, backlog_policy)."""
    t = re.sub(r"\s+", " ", text or "")
    cgpa = pct = None
    for m in re.finditer(r"(\d(?:\.\d+)?)\s*(?:cgpa|gpa)\b", t, re.IGNORECASE):
        v = float(m.group(1))
        if v <= 10:
            cgpa = v if cgpa is None else max(cgpa, v)
        elif v <= 100:                      # "65 CGPA" written on a percentage scale
            pct = v if pct is None else max(pct, v)
    for m in re.finditer(r"(\d{2}(?:\.\d+)?)\s*%", t):
        v = float(m.group(1))
        if 30 <= v <= 100:
            pct = v if pct is None else max(pct, v)
    backlog = ""
    if re.search(r"no\s+active\s+backlogs?", t, re.IGNORECASE):
        backlog = "no_active_backlogs"
    elif re.search(r"no\s+backlogs?", t, re.IGNORECASE):
        backlog = "no_backlogs"
    return cgpa, pct, backlog


# ---------------------------------------------------------------- deadline

_MONTHS = r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?"


def parse_deadline(text: str) -> str | None:
    """ISO date for 'last date to apply' style phrases, else None."""
    t = re.sub(r"\s+", " ", text or "")
    patterns = [
        rf"(?:last\s+date(?:\s+to\s+apply)?|deadline|apply\s+(?:by|before)|closing\s+date|register\s+before)[:\s]*"
        rf"(\d{{1,2}})\s*(?:st|nd|rd|th)?\s*({_MONTHS})\s*,?\s*(\d{{4}})",
        rf"(\d{{1,2}})\s*({_MONTHS})\s*,?\s*(\d{{4}})\s*(?:is\s+the\s+)?(?:last\s+date|deadline)",
        rf"(?:last\s+date|deadline)[:\s]*(\d{{1,2}})[/-](\d{{1,2}})[/-](\d{{4}})",
    ]
    for p in patterns:
        m = re.search(p, t, re.IGNORECASE)
        if m:
            if "/" in p or "[/-]" in p:      # dd/mm/yyyy or dd-mm-yyyy style
                try:
                    dt = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                    if 2000 <= dt.year <= 2100:
                        return dt.isoformat()
                except ValueError:
                    continue
            d = int(m.group(1))
            mon = re.match(r"([a-z]+)", m.group(2).lower()).group(1)[:3]
            try:
                dt = datetime.strptime(f"{d} {mon} {m.group(3)}", "%d %b %Y").date()
                if 2000 <= dt.year <= 2100:
                    return dt.isoformat()
            except ValueError:
                continue
    return None


# ---------------------------------------------------------------- drive flags + fraud

def parse_flags(title: str, text: str) -> tuple[bool, bool, bool]:
    t = f"{title} {text}".lower()
    fresher = bool(re.search(r"\b(?:fresher|fresh\s+graduates?|entry[- ]level|0\s*(?:-|\s*to\s*)\s*[12]?\s*years?|graduate\s+engineer|off\s+campus\s+(?:drive|hiring)|202[5-9]\s+batch)", t)) \
        or "internship" == parse_employment_type(title, text)
    walk_in = bool(re.search(r"\bwalk[- ]?in\b", t))
    off_campus = bool(re.search(r"\boff[- ]campus\b", t))
    return fresher, walk_in, off_campus


# Known-good application hosts (ATS boards run by real recruitment systems).
TRUSTED_APPLY_HOSTS = (
    "greenhouse.io", "lever.co", "ashbyhq.com", "myworkdayjobs.com", "myworkdaysite.com",
    "smartrecruiters.com", "workable.com", "successfactors.com", "taleo.net", "icims.com",
    "eightfold.ai", "phenompeople.com", "phenom.com", "oraclecloud.com", "darwinbox.in",
    "zohorecruit.com", "freshteam.com", "superset.io", "naukri.com", "instahyre.com",
    "linkedin.com", "amazon.jobs", "careers.microsoft.com", "google.com", "amazon.in",
)
FREE_MAIL = ("gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "rediffmail.com", "mail.ru")
PAYMENT_WORDS = (
    r"registration\s+fee", r"pay\s+(?:rs\.?|₹|inr)\s*\d+", r"security\s+deposit",
    r"training\s+fee", r"consultancy\s+fee", r"refundable\s+deposit",
)


def detect_fraud(apply_url: str, company: str, text: str) -> list[str]:
    """Heuristic warnings - never auto-blocks, always surfaced to the user."""
    flags: list[str] = []
    t = (text or "").lower()
    for p in PAYMENT_WORDS:
        if re.search(p, t):
            flags.append("asks for a payment/registration fee - verify before applying")
            break
    url = (apply_url or "").lower()
    if url and not url.startswith(("http://", "https://")):
        flags.append("apply URL is not a proper http(s) link")
    host = ""
    try:
        m = re.search(r"https?://([^/:?#]+)", url)
        host = (m.group(1) if m else "").lower()
    except re.error:
        host = ""
    if host and any(host.endswith(f".{d}") or host == d for d in FREE_MAIL):
        flags.append("apply link points to a free email-style domain")
    if company and host:
        # Company name totally absent from the apply host AND host not a known ATS/board?
        token = company.split()[0].lower() if company.split() else ""
        if token and token not in host and not any(h in host for h in TRUSTED_APPLY_HOSTS):
            flags.append(f"apply host '{host}' doesn't match the company or a known ATS - verify it is official")
    if re.search(r"\burgent(?:ly)?\s+hiring\b.{0,120}\b(no\s+interview|guaranteed\s+(?:job|offer))", t):
        flags.append("'guaranteed job / no interview' claim - typical scam wording")
    return flags


# ---------------------------------------------------------------- entry point

def enrich(title: str, description: str, company: str = "", apply_url: str = "",
           posted_at: str | None = None) -> JobFacts:
    """Extract every deterministic fact from one posting."""
    text = f"{title}\n{description or ''}"
    emp = parse_employment_type(title, description or "")
    exp_lo, exp_hi = parse_experience(text, emp)
    cgpa, pct, backlog = parse_academics(text)
    lo, hi, raw, stipend = parse_salary(text)
    fresher, walk_in, off_campus = parse_flags(title, description or "")
    deadline = parse_deadline(text)
    if not fresher and exp_lo == 0.0:
        fresher = True
    return JobFacts(
        salary_min_lpa=lo, salary_max_lpa=hi, salary_text=raw, stipend_inr=stipend,
        employment_type=emp if emp != "full_time" else "",
        remote_type=parse_remote_type(title, description or ""),
        experience_min=exp_lo, experience_max=exp_hi,
        batch_years=parse_batches(text),
        min_cgpa=cgpa, min_percentage=pct, backlog_policy=backlog,
        deadline=deadline, fresher_flag=fresher, walk_in=walk_in, off_campus=off_campus,
        fraud_flags=detect_fraud(apply_url, company, description or ""),
    )


def apply_to_job(job_id: str, facts: JobFacts) -> None:
    """Persist enrichment onto a jobs row (called after discovery and by `jobagent enrich`)."""
    import json as _json

    from jobagent import db
    db.update(job_id,
              salary_min=facts.salary_min_lpa, salary_max=facts.salary_max_lpa,
              salary_text=facts.salary_text or "", stipend=facts.stipend_inr,
              employment_type=facts.employment_type or "", remote_type=facts.remote_type or "",
              experience_min=facts.experience_min, experience_max=facts.experience_max,
              batch_years=facts.batch_years, min_cgpa=facts.min_cgpa,
              min_percentage=facts.min_percentage, backlog_policy=facts.backlog_policy or "",
              deadline=facts.deadline or "", fresher_flag=int(facts.fresher_flag),
              walk_in=int(facts.walk_in), off_campus=int(facts.off_campus),
              fraud_flags=facts.fraud_flags, enriched_at=db.now())


def enrich_pending(only_missing: bool = True) -> int:
    """(Re)run enrichment for stored jobs. Returns how many were enriched."""
    from jobagent import db
    with db.connect() as conn:
        where = "WHERE enriched_at IS NULL" if only_missing else ""
        rows = conn.execute(f"SELECT id, title, description, company, apply_url FROM jobs {where}").fetchall()
    for r in rows:
        facts = enrich(r["title"], r["description"] or "", r["company"] or "", r["apply_url"] or "")
        apply_to_job(r["id"], facts)
    return len(rows)
