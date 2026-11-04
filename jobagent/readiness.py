"""Phase 18.7 — application readiness score.

A deterministic readiness evaluation of an application BEFORE submission.
Nine dimensions from persisted data only (no LLM required to score):

  1. candidate eligibility      (eligibility_json status)
  2. resume relevance           (match score)
  3. skill coverage             (required vs possessed skills from match_json)
  4. resume formatting          (file exists, non-trivial, PDF)
  5. keyword alignment          (JD keywords present in the tailored resume)
  6. screening answer completeness (saved answers / prepared answers)
  7. missing required fields    (apply session checkpoint data when present)
  8. factual consistency        (resume facts vs profile facts)
  9. application URL validity   (verification state)

Outputs: overall score 0-100, blocking issues, warnings, improvements, and a
submission_ready verdict. Honest by construction: unknown dimensions cost
points, they are never assumed perfect.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from jobagent import db

BLOCKING = "blocking"
WARNING = "warning"
IMPROVEMENT = "improvement"

_STOP = re.compile(r"\b(the|and|for|with|you|your|our|will|are|have|has|must|should|any|all|"
                   r"from|that|this|who|job|role|work|working|team|strong|good|excellent|"
                   r"ability|years?|experience|etc)\b", re.I)


def _jd_keywords(job, limit: int = 25) -> list[str]:
    text = (job["description"] or "")[:8000].lower()
    words = re.findall(r"[a-z][a-z+#.\-]{2,}", text)
    freq: dict[str, int] = {}
    for w in words:
        if _STOP.match(w):
            continue
        freq[w] = freq.get(w, 0) + 1
    return [w for w, _ in sorted(freq.items(), key=lambda kv: -kv[1])[:limit]]


def _tailored_resume_text(job) -> str:
    if not job["resume_path"]:
        return ""
    resume_json = Path(job["resume_path"]).parent / "resume.json"
    try:
        data = json.loads(resume_json.read_text(encoding="utf-8"))
        resume = data.get("resume", data)
        parts = [resume.get("summary", "")]
        for grp in resume.get("skills", []):
            items = grp.get("items", []) if isinstance(grp, dict) else grp
            parts.append(" ".join(map(str, items)))
        for exp in resume.get("experience", []):
            parts.append(exp.get("title", ""))
            parts.extend(exp.get("bullets", []))
        for p in resume.get("projects", []):
            parts.append(p.get("name", ""))
            parts.extend(p.get("bullets", []))
        return " ".join(str(x) for x in parts if x).lower()
    except (ValueError, OSError):
        return ""


def score(job_id: str) -> dict[str, Any]:
    """Full readiness evaluation. Every dimension is scored honestly:
    unknown data yields partial credit with a warning, never full credit."""
    job = db.get(job_id)
    if not job:
        raise KeyError(job_id)

    dims: dict[str, float] = {}
    issues: list[dict[str, str]] = []

    def flag(kind: str, text: str) -> None:
        issues.append({"type": kind, "issue": text})

    # 1. eligibility
    try:
        elig = json.loads(job["eligibility_json"] or "{}").get("status", "")
    except ValueError:
        elig = ""
    dims["candidate_eligibility"] = {"eligible": 1.0, "likely_eligible": 0.7,
                                     "needs_verification": 0.4}.get(elig, 0.0)
    if elig == "not_eligible":
        flag(BLOCKING, f"eligibility status is not_eligible")
    elif not elig:
        flag(WARNING, "eligibility not evaluated yet (run the analyze queue action)")

    # 2. resume relevance (match score)
    ms = job["score"] or job["prefit"] or 0
    dims["resume_relevance"] = max(0.0, min(1.0, ms / 100.0))
    if ms < 60:
        flag(BLOCKING, f"match score {ms} is below the 60 application floor")
    elif ms < 70:
        flag(WARNING, f"match score {ms} is below the configured floor of 70")

    # 3. skill coverage from the last matcher run
    try:
        match = json.loads(job["match_json"] or "{}")
    except ValueError:
        match = {}
    missing = match.get("missing_skills") or match.get("missing_requirements") or []
    if missing:
        dims["skill_coverage"] = 0.6
        flag(IMPROVEMENT, "missing skills: " + ", ".join(missing[:5]))
    elif match:
        dims["skill_coverage"] = 1.0
    else:
        dims["skill_coverage"] = 0.5
        flag(WARNING, "no matcher breakdown stored - skill coverage unknown")

    # 4. resume formatting / presence
    rp = job["resume_path"]
    if rp and Path(rp).exists() and Path(rp).suffix.lower() == ".pdf":
        size_ok = Path(rp).stat().st_size > 10_000
        dims["resume_formatting"] = 1.0 if size_ok else 0.6
        if not size_ok:
            flag(WARNING, "tailored resume file looks too small - regenerate it")
    elif rp:
        dims["resume_formatting"] = 0.5
        flag(BLOCKING, "tailored resume file is missing on disk - re-run prepare")
    else:
        dims["resume_formatting"] = 0.0
        flag(BLOCKING, "no tailored resume - run the prepare queue action")

    # 5. keyword alignment (JD keywords appearing in the tailored resume)
    resume_text = _tailored_resume_text(job)
    if resume_text:
        kws = _jd_keywords(job)
        if kws:
            hit = sum(1 for k in kws if k in resume_text)
            ratio = hit / len(kws)
            dims["keyword_alignment"] = min(1.0, 0.5 + ratio)
            if ratio < 0.25:
                flag(IMPROVEMENT,
                     "tailored resume mentions few of the JD's own keywords - "
                     "tailor it closer to this posting")
        else:
            dims["keyword_alignment"] = 0.5
    else:
        dims["keyword_alignment"] = 0.0
        flag(WARNING, "tailored resume content unavailable for keyword check")

    # 6. screening answer completeness
    has_cover = bool(job["cover_path"]) and Path(job["cover_path"]).exists()
    from jobagent import db as _db
    saved = len(_db.list_answers())
    if has_cover:
        dims["screening_answers"] = 0.8 + min(0.2, saved / 25)
    else:
        dims["screening_answers"] = min(0.5, saved / 25)
        flag(IMPROVEMENT, "no cover letter prepared for this application")

    # 7. missing required fields (from the last apply checkpoint, if any)
    session = db.load_session(job_id)
    state = {}
    if session:
        try:
            state = json.loads(session["state_json"] or "{}")
        except ValueError:
            state = {}
    last = state.get("last_report") or {}
    needs_human = last.get("needs_human") or []
    if needs_human:
        dims["required_fields"] = 0.3
        flag(BLOCKING, "form fields awaiting your decision: " + "; ".join(needs_human[:5]))
    elif state:
        dims["required_fields"] = 1.0
    else:
        dims["required_fields"] = 0.5                    # form not opened yet: unknown
        flag(WARNING, "application form not opened yet - field completeness unknown")

    # 8. factual consistency: resume graduation year vs profile
    try:
        from jobagent.profile import load as load_profile
        from jobagent.resume import load_master
        profile = load_profile()
        resume = load_master()
        mismatch = []
        if profile.graduation_year and resume.education:
            years = [e.year_of_passing or (e.end and int(e.end[:4]))
                     for e in resume.education if e.year_of_passing or e.end]
            if years and profile.graduation_year not in years:
                mismatch.append(f"profile graduation {profile.graduation_year} vs resume {years}")
        if profile.college and resume.education and \
                profile.college.lower() not in json.dumps(
                    [e.model_dump() for e in resume.education]).lower():
            mismatch.append("profile college not found in the resume education")
        if mismatch:
            dims["factual_consistency"] = 0.5
            flag(WARNING, "profile vs resume mismatch: " + "; ".join(mismatch))
        else:
            dims["factual_consistency"] = 1.0
    except (SystemExit, Exception):                       # noqa: BLE001 - no resume yet
        dims["factual_consistency"] = 0.5
        flag(WARNING, "factual consistency not checkable (master resume missing)")

    # 9. application URL validity
    vstate = job["verification_state"] or "unverified"
    dims["url_validity"] = {"verified_open": 1.0, "probably_open": 0.8,
                            "needs_manual": 0.4}.get(vstate, 0.0)
    if vstate in ("expired", "unavailable", "suspicious"):
        flag(BLOCKING, f"apply URL verification state is {vstate}")
    elif vstate == "unverified":
        flag(WARNING, "apply URL not verified yet")

    # ---- aggregate --------------------------------------------------------
    weights = {"candidate_eligibility": 0.18, "resume_relevance": 0.14,
               "skill_coverage": 0.08, "resume_formatting": 0.12,
               "keyword_alignment": 0.10, "screening_answers": 0.10,
               "required_fields": 0.12, "factual_consistency": 0.06,
               "url_validity": 0.10}
    overall = round(100 * sum(dims[k] * w for k, w in weights.items()))
    blocking = [i["issue"] for i in issues if i["type"] == BLOCKING]
    warnings = [i["issue"] for i in issues if i["type"] == WARNING]
    improvements = [i["issue"] for i in issues if i["type"] == IMPROVEMENT]

    return {
        "job_id": job_id,
        "overall": overall,
        "dimensions": {k: round(v * 100) for k, v in dims.items()},
        "blocking_issues": blocking,
        "warnings": warnings,
        "improvements": improvements,
        "submission_ready": overall >= 70 and not blocking,
        "ready_for_authorized_submission": overall >= 85 and not blocking,
    }
