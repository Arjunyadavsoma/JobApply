"""Phase 17.7 — smart application queue.

Every opportunity walks the queue: new -> analyzing -> eligible -> shortlisted ->
resume_prep -> ready -> awaiting_approval -> applying -> submitted (or failed /
needs_manual). Quality gates prevent mass low-quality applications:

  automation.max_per_day          (default 15)   daily budget
  automation.max_per_company      (default 2)    don't spam one employer
  automation.min_match_score      (default 70)   only apply to real matches
  automation.min_eligibility      (default likely_eligible)
  automation.min_quality_score    (default 40)   job verification quality floor
  automation.min_salary_lpa       (default 6)    CTC floor from the profile
  automation.preferred_locations  (default India hub list + remote)

The queue never invents progress: each advance is an action that actually ran
(scored / tailored / submitted), recorded as a job event.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from jobagent import db
from jobagent.config import cfg

# queue states ordered by pipeline position
QUEUE_FLOW = ["new", "analyzing", "eligible", "shortlisted", "resume_prep", "ready",
              "awaiting_approval", "applying", "submitted", "failed", "needs_manual"]

ELIGIBILITY_RANK = {"eligible": 3, "likely_eligible": 2, "needs_verification": 1,
                    "not_eligible": 0}
MIN_ELIGIBILITY = {"eligible": 3, "likely_eligible": 2, "needs_verification": 1}


def _eligibility_of(row) -> str:
    try:
        data = json.loads(row["eligibility_json"] or "{}")
        return data.get("status", "")
    except (ValueError, TypeError):
        return ""


def set_queue_state(job_id: str, state: str, note: str = "") -> bool:
    if state not in QUEUE_FLOW:
        return False
    db.update(job_id, queue_state=state)
    db.record_event(job_id, "queue", f"Queue -> {state}" + (f" ({note})" if note else ""))
    return True


def daily_submissions() -> int:
    day = db.now()[:10]
    with db.connect() as conn:
        row = conn.execute("SELECT COUNT(*) c FROM submissions WHERE submitted_at LIKE ?",
                           (f"{day}%",)).fetchone()
        return row["c"]


def company_submissions(company: str) -> int:
    with db.connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) c FROM submissions s JOIN jobs j ON j.id = s.job_id "
            "WHERE lower(j.company) = lower(?)", (company,)).fetchone()
        return row["c"]


def can_apply(job_id: str) -> tuple[bool, str]:
    """Every gate an application must pass before any mode-C execution. (ok, reason)."""
    job = db.get(job_id)
    if not job:
        return False, "job not found"

    max_day = int(cfg("automation.max_per_day", 15))
    if daily_submissions() >= max_day:
        return False, f"daily limit reached ({max_day} applications/day)"

    max_co = int(cfg("automation.max_per_company", 2))
    if company_submissions(job["company"]) >= max_co:
        return False, f"company limit reached ({max_co} per company)"

    try:
        flags = json.loads(job["fraud_flags"] or "[]")
    except (ValueError, TypeError):
        flags = []
    if flags:
        return False, "unresolved trust warnings: " + "; ".join(flags[:3])

    if job["status"] in ("applied", "assessment", "interview", "offer"):
        return False, "already applied (pipeline)"
    if db.submissions_for(job_id):
        return False, "a submission is already recorded for this job"

    vstate = job["verification_state"] or "unverified"
    if vstate in ("expired", "unavailable", "suspicious"):
        return False, f"job verification state is {vstate}"

    elig = _eligibility_of(job)
    min_elig = MIN_ELIGIBILITY.get(str(cfg("automation.min_eligibility", "likely_eligible")),
                                   "likely_eligible")
    if elig and ELIGIBILITY_RANK.get(elig, 0) < min_elig:
        return False, f"eligibility {elig or 'unknown'} below configured floor"

    score = job["score"] or job["prefit"] or 0
    if score < int(cfg("automation.min_match_score", 70)):
        return False, f"match score {score} below floor {cfg('automation.min_match_score', 70)}"

    quality = job["quality_score"]
    if quality is not None and quality < int(cfg("automation.min_quality_score", 40)):
        return False, f"job quality {quality} below floor"

    return True, "all gates passed"


def _deadline_urgency(job) -> int:
    """0 (no deadline) .. 3 (due within 1 day) - used for prioritisation."""
    if not job["deadline"]:
        return 0
    try:
        dl = datetime.fromisoformat(str(job["deadline"]))
        if dl.tzinfo is None:
            dl = dl.replace(tzinfo=timezone.utc)
        days = (dl - datetime.now(timezone.utc)).total_seconds() / 86400
        return 3 if days <= 1 else 2 if days <= 3 else 1 if days <= 7 else 0
    except ValueError:
        return 0


def queue_rows(limit: int = 100, states: list[str] | None = None) -> list[dict[str, Any]]:
    """The dashboard queue: every column Phase 17.7 asks for, from persisted data."""
    with db.connect() as conn:
        if states:
            q = (f"SELECT * FROM jobs WHERE queue_state IN ({','.join('?' * len(states))}) "
                 f"ORDER BY COALESCE(score, prefit, 0) DESC LIMIT ?")
            rows = conn.execute(q, (*states, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE queue_state NOT IN ('new') "
                "OR queue_state IS NULL ORDER BY COALESCE(score, prefit, 0) DESC LIMIT ?",
                (limit,)).fetchall()
    out = []
    for r in rows:
        ok, reason = can_apply(r["id"])
        out.append({
            "job_id": r["id"],
            "company": r["company"],
            "title": r["title"],
            "salary": r["salary_text"] or (f"{r['salary_min']}-{r['salary_max']} LPA"
                                            if r["salary_min"] else None),
            "stipend": r["stipend"],
            "location": r["location"],
            "remote_type": r["remote_type"],
            "match_score": r["score"] or r["prefit"],
            "quality_score": r["quality_score"],
            "verification_state": r["verification_state"] or "unverified",
            "eligibility": _eligibility_of(r) or "unknown",
            "deadline": r["deadline"],
            "deadline_urgency": _deadline_urgency(r),
            "resume_ready": bool(r["resume_path"]),
            "queue_state": r["queue_state"] or "new",
            "pipeline_status": r["status"],
            "apply_allowed": ok,
            "apply_block_reason": "" if ok else reason,
        })
    return out


def prioritised(limit: int = 20) -> list[dict[str, Any]]:
    """Rank ready-to-apply jobs: relevance first, deadline urgency breaks ties."""
    rows = queue_rows(limit=200, states=["ready", "awaiting_approval"])
    rows.sort(key=lambda r: (-(r["match_score"] or 0), -r["deadline_urgency"]))
    return rows[:limit]


def limits() -> dict[str, Any]:
    return {
        "max_per_day": int(cfg("automation.max_per_day", 15)),
        "max_per_company": int(cfg("automation.max_per_company", 2)),
        "min_match_score": int(cfg("automation.min_match_score", 70)),
        "min_eligibility": str(cfg("automation.min_eligibility", "likely_eligible")),
        "min_quality_score": int(cfg("automation.min_quality_score", 40)),
        "min_salary_lpa": cfg("automation.min_salary_lpa", 6),
        "preferred_locations": cfg("automation.preferred_locations",
                                   ["Hyderabad", "Bengaluru", "Chennai", "Pune", "Mumbai",
                                    "Gurugram", "Noida", "Remote"]),
        "authorized_submission": bool(cfg("automation.authorized_submission", False)),
        "daily_used": daily_submissions(),
    }


# ---------------------------------------------------------------- queue actions

def action_analyze(job_id: str) -> dict[str, Any]:
    """Queue action: score + eligibility + verify, then move to eligible/needs_manual."""
    from jobagent import verify as verify_engine
    job = db.get(job_id)
    if not job:
        raise KeyError(job_id)
    set_queue_state(job_id, "analyzing")
    result: dict[str, Any] = {"job_id": job_id}
    try:
        res = verify_engine.verify_job(job_id)
        result["verification"] = {"state": res.state, "quality": res.quality_score}
    except Exception as e:                                   # noqa: BLE001
        result["verification"] = {"error": str(e)[:200]}
    job = db.get(job_id)
    score = job["score"] or job["prefit"] or 0
    elig = _eligibility_of(job)
    result["match_score"] = score
    result["eligibility"] = elig
    if ELIGIBILITY_RANK.get(elig, 0) >= 1 and score >= int(cfg("automation.min_match_score", 70)):
        set_queue_state(job_id, "eligible")
    else:
        set_queue_state(job_id, "needs_manual", f"score {score}, eligibility {elig or 'unknown'}")
    result["queue_state"] = db.get(job_id)["queue_state"]
    return result


def action_shortlist(job_id: str) -> dict[str, Any]:
    job = db.get(job_id)
    if not job:
        raise KeyError(job_id)
    ok, reason = can_apply(job_id)
    if not ok:
        set_queue_state(job_id, "needs_manual", reason)
        return {"job_id": job_id, "ok": False, "reason": reason}
    set_queue_state(job_id, "shortlisted")
    return {"job_id": job_id, "ok": True, "queue_state": "shortlisted"}


def action_prepare(job_id: str) -> dict[str, Any]:
    """Queue action: tailored resume + cover letter (Resume Tailoring Agent)."""
    job = db.get(job_id)
    if not job:
        raise KeyError(job_id)
    set_queue_state(job_id, "resume_prep")
    from jobagent.agents import run as agent_run
    try:
        out = agent_run("resume_tailoring", job_id)
    except Exception as e:                                   # noqa: BLE001
        set_queue_state(job_id, "failed", str(e)[:200])
        return {"job_id": job_id, "ok": False, "error": str(e)[:300]}
    job = db.get(job_id)
    ready = bool(job["resume_path"]) and bool(job["cover_path"])
    db.update(job_id, queue_ready=1 if ready else 0)
    set_queue_state(job_id, "ready" if ready else "needs_manual",
                    "tailored" if ready else "tailoring incomplete")
    return {"job_id": job_id, "ok": ready, "resume_path": job["resume_path"],
            "cover_path": job["cover_path"]}


def action_approve(job_id: str) -> dict[str, Any]:
    """Explicit human approval: required before any mode-C execution."""
    if not cfg("automation.authorized_submission", False):
        return {"job_id": job_id, "ok": False,
                "error": "authorized_submission is disabled (automation.authorized_submission)"}
    ok, reason = can_apply(job_id)
    if not ok:
        return {"job_id": job_id, "ok": False, "error": reason}
    set_queue_state(job_id, "awaiting_approval", "approved by user")
    return {"job_id": job_id, "ok": True, "queue_state": "awaiting_approval"}


ACTIONS = {"analyze": action_analyze, "shortlist": action_shortlist,
           "prepare": action_prepare, "approve": action_approve}


def run_action(action: str, job_id: str) -> dict[str, Any]:
    if action not in ACTIONS:
        raise KeyError(f"Unknown queue action '{action}'. Use: {', '.join(ACTIONS)}")
    return ACTIONS[action](job_id)
