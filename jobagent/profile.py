"""Candidate profile: the editable single source of truth for eligibility + form answers.

Stored in profile/candidate_profile.json. On first run it is seeded from (in order):
  1. the existing `applicant:` facts in config.yaml, and
  2. sensible India-first defaults the user asked for (B.Tech CSE AIML, 2027 batch, Hyderabad,
     AI/ML/software roles, ₹6+ LPA) - as *editable defaults*, never as hardcoded rules.

Never infers missing details; unknown stays unknown so eligibility says needs_verification.
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from jobagent.config import PROFILE_DIR, cfg
from jobagent.models import CandidateProfile

PROFILE_JSON = PROFILE_DIR / "candidate_profile.json"

# Editable defaults requested for the initial setup (kept ONLY as seed data).
INDIA_DEFAULTS: dict = {
    "candidate_type": "fresher",
    "graduation_year": 2027,
    "degree": "B.Tech",
    "branch": "Computer Science and Engineering (Artificial Intelligence and Machine Learning)",
    "current_location": "Hyderabad, India",
    "preferred_roles": ["AI Engineer", "ML Engineer", "Software Engineer", "Python Developer"],
    "secondary_roles": ["Data Analyst", "Data Scientist", "Backend Developer", "GenAI Engineer"],
    "preferred_locations": ["Hyderabad", "Bengaluru", "Pune", "Chennai", "Mumbai", "Gurugram", "Noida"],
    "open_to_remote": True,
    "open_to_relocation": True,
    "expected_ctc_lpa": 6.0,
    "work_authorization": "Indian citizen; authorized to work in India",
    "skills": ["Python", "Machine Learning", "SQL", "Pandas", "NumPy", "scikit-learn",
               "Deep Learning", "TensorFlow", "PyTorch", "Git"],
}


def _from_config() -> dict:
    """Carry over the applicant facts from config.yaml (backwards compatibility)."""
    from jobagent.config import CONFIG_PATH
    if not CONFIG_PATH.exists():
        return {}
    a = cfg("applicant", {}) or {}
    mapping = {
        "phone": "phone", "email": "email", "current_location": "current_location",
        "linkedin_url": "linkedin_url", "github_url": "github_url",
        "portfolio_url": "portfolio_url", "work_authorization": "work_authorization",
        "notice_period": "notice_period", "willing_to_relocate": "open_to_relocation",
    }
    out: dict = {}
    for src, dst in mapping.items():
        v = a.get(src)
        if isinstance(v, str) and v.strip():
            out[dst] = v if dst != "open_to_relocation" else (v.strip().lower().startswith("y"))
    for key in ("current_ctc", "expected_ctc"):
        v = str(a.get(key, "") or "").strip()
        if v:
            try:
                num = float(v.split()[0])
                if "lpa" in v.lower() or num <= 100:
                    out["current_ctc_lpa" if key == "current_ctc" else "expected_ctc_lpa"] = num
            except ValueError:
                pass
    m = str(a.get("total_experience_years", "") or "")
    if m.strip():
        import re
        mm = re.search(r"\d+(\.\d+)?", m)
        if mm:
            out["total_experience_years"] = float(mm.group())
    # extra facts (how_did_you_hear, gender, ...) are kept for form filling
    extras = {k: str(v) for k, v in a.items()
              if isinstance(v, str) and v.strip() and k not in
              ("phone", "email", "current_location", "linkedin_url", "github_url", "portfolio_url",
               "work_authorization", "notice_period", "willing_to_relocate", "current_ctc",
               "expected_ctc", "total_experience_years")}
    if extras:
        out["extra"] = extras
    return out


def load() -> CandidateProfile:
    if PROFILE_JSON.exists():
        return CandidateProfile.model_validate_json(PROFILE_JSON.read_text(encoding="utf-8"))
    # First run: seed from config + India defaults (name/email from master resume if present).
    seed: dict = {**INDIA_DEFAULTS, **_from_config()}
    from jobagent.config import MASTER_RESUME_JSON
    if MASTER_RESUME_JSON.exists():
        try:
            import json
            r = json.loads(MASTER_RESUME_JSON.read_text(encoding="utf-8"))
            seed.setdefault("full_name", r.get("name", ""))
            seed.setdefault("email", r.get("email", ""))
            seed.setdefault("phone", r.get("phone", "") or "")
            skills: list[str] = []
            for g in r.get("skills", []):
                skills += [s for s in g.get("items", []) if len(s) <= 30]
            for p in r.get("projects", []):
                skills += [t for t in p.get("tech", []) if len(t) <= 30]
            if skills:
                seen: list[str] = []
                for s in skills:
                    if s not in seen:
                        seen.append(s)
                seed["skills"] = seen[:40]
            for e in r.get("education", []):
                if "b.tech" in (e.get("degree") or "").lower() or "b.e" in (e.get("degree") or "").lower():
                    seed.setdefault("college", e.get("institution", ""))
        except (ValueError, KeyError):
            pass
    p = CandidateProfile(**{k: v for k, v in seed.items() if k in CandidateProfile.model_fields})
    save(p)
    return p


def save(profile: CandidateProfile) -> None:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_JSON.write_text(profile.model_dump_json(indent=2), encoding="utf-8")


def facts_for_forms() -> dict[str, str]:
    """Flat key/value facts used by the form-filling LLM prompt (resume facts come separately)."""
    p = load()
    out: dict[str, str] = {
        "full_name": p.full_name, "email": p.email, "phone": p.phone,
        "current_location": p.current_location, "linkedin_url": p.linkedin_url,
        "github_url": p.github_url, "portfolio_url": p.portfolio_url,
        "graduation_year": str(p.graduation_year or ""), "degree": p.degree, "branch": p.branch,
        "college": p.college, "university": p.university,
        "cgpa": f"{p.cgpa:g}/{p.cgpa_scale:g}" if p.cgpa is not None else "",
        "tenth_percentage": f"{p.tenth_percentage:g}%" if p.tenth_percentage is not None else "",
        "twelfth_percentage": f"{p.twelfth_percentage:g}%" if p.twelfth_percentage is not None else "",
        "active_backlogs": str(p.active_backlogs), "previous_backlogs": str(p.previous_backlogs),
        "total_experience_years": f"{p.total_experience_years:g}" if p.total_experience_years else "0",
        "work_authorization": p.work_authorization,
        "willing_to_relocate": "Yes" if p.open_to_relocation else "No",
        "notice_period": p.notice_period,
        "current_ctc": f"{p.current_ctc_lpa:g} LPA" if p.current_ctc_lpa else "",
        "expected_ctc": f"{p.expected_ctc_lpa:g} LPA" if p.expected_ctc_lpa else "",
        "preferred_work_location": ", ".join(p.preferred_locations),
        "preferred_roles": ", ".join(p.preferred_roles),
        "internship_availability": "Yes" if p.candidate_type in ("fresher", "entry_level") or
                                   p.total_experience_years < 2 else "",
        "how_did_you_hear": p.extra.get("how_did_you_hear", "Company careers page"),
    }
    out.update({k: v for k, v in p.extra.items() if v})
    return {k: v for k, v in out.items() if v}


class ProfileUpdate(BaseModel):
    """Any subset of CandidateProfile fields, validated by pydantic on the API side."""
    pass
