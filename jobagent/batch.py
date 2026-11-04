"""Phase 18.8 — controlled batch application workflow.

Discover -> Verify -> Match -> Shortlist -> PREPARE -> Review -> Approve ->
Submit -> Track. Batches make PREPARATION parallel-safe and auditable; they
never mass-submit. Submission stays a per-job, per-approval action behind the
mode-C gates exactly as in Phase 17.6.

Controls (each checked per job, on top of queue.can_apply):
  allowed_companies / excluded_companies
  allowed_locations
  allowed_job_types        (internship | full_time | ...)
  max_batch_size           (default 10)
  min_match_score / min_quality_score / min_eligibility

Every job in a batch gets its own preparation run and its own audit trail
(batch_runs row + job events). Duplicate protection: a job already submitted
or already in another active batch is skipped with a reason.
"""
from __future__ import annotations

import json
from typing import Any

from jobagent import db
from jobagent.config import cfg

DEFAULT_MAX_BATCH = 10


def _controls(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Effective controls: config defaults <- caller overrides."""
    base = {
        "allowed_companies": [str(c) for c in cfg("batch.allowed_companies", [])],
        "excluded_companies": [str(c) for c in cfg("batch.excluded_companies", [])],
        "allowed_locations": [str(c) for c in cfg("batch.allowed_locations", [])],
        "allowed_job_types": [str(c) for c in cfg("batch.allowed_job_types", [])],
        "max_batch_size": int(cfg("batch.max_size", DEFAULT_MAX_BATCH)),
        "min_match_score": int(cfg("automation.min_match_score", 70)),
        "min_quality_score": int(cfg("automation.min_quality_score", 40)),
        "min_eligibility": str(cfg("automation.min_eligibility", "likely_eligible")),
        "max_per_day": int(cfg("automation.max_per_day", 15)),
        "max_per_company": int(cfg("automation.max_per_company", 2)),
    }
    if overrides:
        for k, v in overrides.items():
            if k in base and v is not None:
                base[k] = v
    return base


def _batch_gate(job, controls: dict[str, Any]) -> str | None:
    """Why this job may NOT enter the batch, or None when it may."""
    company = (job["company"] or "").strip().lower()
    if controls["excluded_companies"] and company in \
            [c.strip().lower() for c in controls["excluded_companies"]]:
        return "company is excluded"
    if controls["allowed_companies"] and company not in \
            [c.strip().lower() for c in controls["allowed_companies"]]:
        return "company not in the allowed list"
    loc = (job["location"] or "").lower()
    if controls["allowed_locations"] and loc and not any(
            a.lower() in loc for a in controls["allowed_locations"]):
        return "location not allowed"
    if controls["allowed_job_types"] and job["employment_type"] and \
            job["employment_type"] not in controls["allowed_job_types"]:
        return f"job type {job['employment_type']} not allowed"
    score = job["score"] or job["prefit"] or 0
    if score < controls["min_match_score"]:
        return f"match score {score} < {controls['min_match_score']}"
    if job["quality_score"] is not None and job["quality_score"] < controls["min_quality_score"]:
        return f"quality {job['quality_score']} < {controls['min_quality_score']}"
    try:
        elig = json.loads(job["eligibility_json"] or "{}").get("status", "")
    except ValueError:
        elig = ""
    from jobagent.queue import ELIGIBILITY_RANK, MIN_ELIGIBILITY
    floor = MIN_ELIGIBILITY.get(controls["min_eligibility"], 2)
    if elig and ELIGIBILITY_RANK.get(elig, 0) < floor:
        return f"eligibility {elig} below floor"
    if db.submissions_for(job["id"]):
        return "already submitted (duplicate protection)"
    if (job["queue_state"] or "") in ("applying", "awaiting_approval"):
        return f"already {job['queue_state']} in another flow"
    return None


def batch_prepare(job_ids: list[str], label: str = "",
                  controls: dict[str, Any] | None = None,
                  skip_llm: bool = False) -> dict[str, Any]:
    """Prepare a batch of shortlisted jobs. Per-job outcomes, never a bulk submit."""
    from jobagent.queue import can_apply
    from jobagent import prep as prep_module

    ctl = _controls(controls)
    job_ids = job_ids[:ctl["max_batch_size"]]
    if not label:
        label = f"batch {db.now()[:16]}"
    batch_id = db.create_batch_run(label, job_ids, ctl)

    results: list[dict[str, Any]] = []
    for jid in job_ids:
        job = db.get(jid)
        if not job:
            results.append({"job_id": jid, "ok": False, "skipped": "job not found"})
            continue
        reason = _batch_gate(job, ctl)
        if reason:
            results.append({"job_id": jid, "company": job["company"],
                            "ok": False, "skipped": reason})
            db.record_event(jid, "queue", f"Batch '{label}' skipped this job: {reason}")
            continue
        # the standing queue gates still apply on top
        ok, why = can_apply(jid)
        if not ok:
            results.append({"job_id": jid, "company": job["company"],
                            "ok": False, "skipped": f"queue gate: {why}"})
            continue
        try:
            out = prep_module.prepare(jid, skip_llm=skip_llm)
            results.append({"job_id": jid, "company": job["company"], "ok": True,
                            "ready": out["ready"],
                            "readiness": out["readiness"]["overall"]})
        except Exception as e:                               # noqa: BLE001
            results.append({"job_id": jid, "company": job["company"], "ok": False,
                            "error": str(e)[:200]})
            from jobagent.queue import set_queue_state
            set_queue_state(jid, "failed", f"batch prepare failed: {str(e)[:120]}")

    summary = {
        "batch_id": batch_id, "label": label,
        "requested": len(job_ids), "prepared": sum(1 for r in results if r.get("ok")),
        "skipped": sum(1 for r in results if not r.get("ok")),
        "controls": ctl, "results": results,
        "next": "review each prepared application, then approve + submit per job",
    }
    db.finish_batch_run(batch_id, summary)
    db.add_notification("status", f"Batch prepared: {summary['prepared']}/{len(job_ids)}",
                        "Review the prepared applications in the queue.", None)
    return summary


def batch_history(limit: int = 20) -> list[dict[str, Any]]:
    rows = db.batch_runs(limit=limit)
    out = []
    for r in rows:
        try:
            result = json.loads(r["result"] or "null")
        except ValueError:
            result = None
        out.append({"id": r["id"], "label": r["label"],
                    "job_ids": json.loads(r["job_ids"] or "[]"),
                    "created_at": r["created_at"], "finished_at": r["finished_at"],
                    "result": result})
    return out
