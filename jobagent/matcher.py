"""Explainable job matching: AI semantic scoring merged with deterministic eligibility.

Pipeline per job:
  1. Deterministic eligibility (eligibility.py) - batch, CGPA, experience, backlogs, fraud.
     This GATES the verdict: a hard fail caps the score and forces verdict "skip".
  2. AI semantic scoring (Groq) on skills / education / experience / location dimensions,
     using the master resume + candidate profile. The AI never sees eligibility as something
     it may override - its verdict only counts when eligibility does not hard-fail.
  3. Merge into MatchResultV2 with per-dimension scores, missing skills and recommendations.
"""
from __future__ import annotations

import json

from jobagent import db
from jobagent.config import cfg
from jobagent.eligibility import evaluate as run_eligibility, row_to_facts, status_priority
from jobagent.llm import LLMError, structured, untrusted
from jobagent.models import CandidateProfile, MatchResultV2, Resume
from jobagent.sources.common import log

SCORE_SYSTEM = """You screen jobs for one candidate and rate how well they fit, as a strict but fair recruiter would.

Scoring guide (overall `score`):
- 85-100: meets essentially all must-haves; strong interview chance.
- 70-84: meets most must-haves; worth applying.
- 50-69: notable gaps (years of experience, core stack, domain) - long shot.
- 0-49: wrong role, level, or location/work-authorization mismatch.
Treat hard requirements (years of experience, required degree, location/visa, must-have tech) as decisive.
Judge only from the resume and profile facts; do not assume skills they don't show.

Give a separate 0-100 score per dimension:
- skills_score: overlap of the job's required tech with the candidate's actual tech
- education_score: degree / branch / academic fit for the role
- experience_score: level and years fit (a fresher role is a GREAT fit for a fresher)
- location_score: matches the candidate's location preferences / remote openness

Candidate profile facts:
{facts}

<resume>
{resume}
</resume>"""


def _priority(job) -> int:
    """Lower = scored first: jobs matching search.priority_locations, in list order."""
    loc = f"{job['location'] or ''} {job['source']}".lower()
    prefs = [p.lower() for p in cfg("search.priority_locations", [])]
    return next((i for i, p in enumerate(prefs) if p in loc), len(prefs))


def _profile_facts(profile: CandidateProfile) -> str:
    lines = [
        f"- candidate_type: {profile.candidate_type}",
        f"- education: {profile.degree} {profile.branch}, graduating {profile.graduation_year}"
        f"{' at ' + profile.college if profile.college else ''}",
        f"- cgpa: {profile.cgpa if profile.cgpa is not None else 'unknown'}",
        f"- experience: {profile.total_experience_years} years",
        f"- current_location: {profile.current_location}",
        f"- preferred_locations: {', '.join(profile.preferred_locations) or 'any'}",
        f"- open_to_remote: {profile.open_to_relocation and profile.open_to_remote}",
        f"- work_authorization: {profile.work_authorization or 'unknown'}",
        f"- target_roles: {', '.join(profile.preferred_roles + profile.secondary_roles)}",
    ]
    return "\n".join(lines)


def score_one(job, master: Resume, profile: CandidateProfile) -> tuple[MatchResultV2, object]:
    """Score a single job: (AI-or-local match, deterministic eligibility).
    When the AI backend is unavailable, falls back to the local prefit estimate so the
    pipeline keeps working; eligibility always comes from the deterministic engine."""
    eligibility = run_eligibility(row_to_facts(job), profile)
    try:
        m = structured(
            MatchResultV2,
            system=SCORE_SYSTEM.format(facts=_profile_facts(profile),
                                       resume=master.model_dump_json(indent=1)),
            content=(f"Company: {job['company']}\nRole: {job['title']}\n"
                     f"Location: {job['location'] or 'n/a'}\n\n"
                     f"{untrusted('job_description', job['description'] or '')}"),
            effort=cfg("llm.score_effort", "low"),
            max_tokens=4000,
            task="score",
            cache=True,
        )
    except LLMError:
        # AI unavailable: fall back to a local, prefit-based estimate with eligibility intact,
        # so the pipeline keeps working offline (score 0 keeps the job visible but unranked).
        m = MatchResultV2(
            score=int(job["prefit"] or 0), verdict="maybe",
            skills_score=int(job["prefit"] or 0), education_score=50, experience_score=50,
            location_score=50, reasons=["Scored locally - AI unavailable, refine later"],
            missing_requirements=[],
        )

    # ---- merge: deterministic eligibility gates the AI verdict ----
    if eligibility.status == "not_eligible":
        worst = eligibility.worst_check()
        m.disqualifiers = m.disqualifiers or [f"{worst.criterion}: needs {worst.required}, "
                                              f"candidate has {worst.candidate}" if worst else "not eligible"]
        m.score = min(m.score, 25)
        m.verdict = "skip"
    elif eligibility.status == "needs_verification" and m.verdict == "apply":
        m.verdict = "maybe"
        m.recommendations = m.recommendations or []
        m.recommendations.insert(0, "Verify the eligibility question marks before applying")
    return m, eligibility


def score_new(master: Resume, limit: int | None = None, job_id: str | None = None,
              profile: CandidateProfile | None = None) -> None:
    from jobagent import prefit
    from jobagent.profile import load as load_profile

    profile = profile or load_profile()
    if job_id:
        jobs = [j for j in [db.get(job_id)] if j]
    else:
        prefit.update()
        # Best estimated fit first (then preferred locations), so a limited run scores the most promising jobs.
        jobs = sorted(db.by_status("new", order="discovered_at DESC"),
                      key=lambda j: (-(j["prefit"] or 0), _priority(j)))[:limit]
    if not jobs:
        log.info("No new jobs to score.")
        return
    threshold = cfg("search.min_match_score", 70)
    for i, job in enumerate(jobs, 1):
        try:
            m, eligibility = score_one(job, master, profile)
        except LLMError as e:
            db.update(job["id"], status="error", error=str(e))
            db.record_event(job["id"], "error", str(e))
            log.error(f"[{i}/{len(jobs)}] {job['company']} - {job['title']}: {e}")
            continue
        status = ("skipped" if m.verdict == "skip" or m.score < threshold
                  or eligibility.status == "not_eligible" else "scored")
        merged = m.model_dump()
        merged["eligibility"] = eligibility.model_dump()
        db.update(job["id"], status=status, score=m.score, match_json=merged, error=None)
        db.record_event(job["id"], "score", f"Match {m.score}/100 ({m.verdict}) - {eligibility.status}")
        if eligibility.status == "not_eligible":
            db.add_notification("status", f"Not eligible: {job['company']} - {job['title']}",
                                "; ".join(c.note or c.criterion for c in eligibility.checks if c.result == "fail")[:300],
                                job["id"])
        log.info(f"[{i}/{len(jobs)}] {m.score:>3}  {status:<8} {job['company']} - {job['title']}"
                 f"  [{eligibility.status}]")
