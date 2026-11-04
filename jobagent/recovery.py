"""Phase 18.9 — application recovery engine.

Applications fail mid-way for boring reasons: the browser crashes, the network
drops, Groq times out, the ATS changes its form, the page session expires. The
recovery engine makes those failures survivable:

  * ONE browser session per application — acquire_apply_lock (SQLite-backed,
    stale after 1h) prevents two drivers fighting over the same form. This is
    also the "never run concurrent sessions on one application" rule.
  * CHECKPOINTS — FlowDriver saves {step, url, ats, field values} after every
    step into apply_sessions.state_json.
  * RESUME — reopen the URL, re-extract, skip every field that already has the
    right value (idempotent fill), continue from the checkpointed step.
  * NO DOUBLE SUBMIT — a resume NEVER clicks submit on a job that already has
    a recorded submission; recovery stops at review pause and asks the human.

None of this re-submits anything: the submit action itself stays behind the
mode-C authorization gates (execution.py).
"""
from __future__ import annotations

from typing import Any

from jobagent import db


class LockHeld(Exception):
    """Another live browser session owns this application."""

    def __init__(self, owner: str):
        super().__init__(f"another apply session holds the lock: {owner}")
        self.owner = owner


class AlreadySubmitted(Exception):
    """A confirmed submission exists for this job — never submit again."""


# ------------------------------------------------------------------ session locks

def acquire(job_id: str, owner: str = "") -> str:
    """Take the per-job browser lock or raise LockHeld. Returns the owner token."""
    owner = owner or f"apply-{db.now()}"
    if not db.acquire_apply_lock(job_id, owner):
        holder = db.apply_lock_holder(job_id) or "unknown"
        raise LockHeld(holder)
    return owner


def release(job_id: str, owner: str | None = None) -> None:
    db.release_apply_lock(job_id, owner)


def holder(job_id: str) -> str | None:
    return db.apply_lock_holder(job_id)


# ------------------------------------------------------------------ checkpoint state

def checkpoint(job_id: str) -> dict[str, Any] | None:
    """The freshest apply-session state (its `state_json`), or None."""
    session = db.load_session(job_id)
    if not session:
        return None
    try:
        import json
        return json.loads(session["state_json"])
    except (ValueError, TypeError, KeyError):
        return None


def steps_completed(job_id: str) -> int:
    state = checkpoint(job_id)
    return int((state or {}).get("step") or 0)


# ------------------------------------------------------------------ resume

def resume(job_id: str, page, *, owner: str = "") -> dict[str, Any]:
    """Continue an interrupted application on an already-open page.

    Returns a report instead of doing anything risky:
      {resumed, step, ats, skipped_filled, already_submitted, next_action}

    The heavy lifting (fill/advance) is delegated back to FlowDriver by the
    caller (the CLI worker / assisted session) — this function prepares the
    idempotent resume: guards, URL, and the fields that must NOT be re-filled.
    """
    job = db.get(job_id)
    if not job:
        raise KeyError(job_id)

    if db.submissions_for(job_id):
        raise AlreadySubmitted(job_id)

    state = checkpoint(job_id) or {}
    step = int(state.get("step") or 0)

    url = state.get("url") or job["apply_url"] or job["url"]
    current = page.url
    if url and current != url:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2000)

    from jobagent.apply import extract_fields, _is_empty
    from jobagent.ats_flow import detect_adapter
    adapter, info = detect_adapter(page.url, page)
    fields = extract_fields(page, read_options=False)
    filled = sum(1 for f in fields.values() if not _is_empty(f))

    next_action = "continue_flow" if fields else "open_form_first"
    db.record_event(job_id, "apply",
                    f"Recovery: resumed at step {step} ({filled} fields already carry values)")
    return {
        "resumed": True,
        "job_id": job_id,
        "step": step,
        "ats": info.get("platform", "generic"),
        "skipped_filled": filled,
        "already_submitted": False,
        "next_action": next_action,
        "url": page.url[:300],
    }


def record_failure(job_id: str, reason: str, screenshot: str = "") -> None:
    """Persist an interruption so the queue + human see it immediately."""
    db.record_event(job_id, "apply", f"Interruption: {reason[:300]}")
    from jobagent.queue import set_queue_state
    set_queue_state(job_id, "needs_manual", reason[:200])
    db.add_notification("warning", "Application interrupted",
                        reason[:300], job_id)
    if screenshot:
        state = checkpoint(job_id) or {}
        state["last_failure"] = {"reason": reason[:300], "screenshot": screenshot}
        db.save_session(job_id, "assisted", state)
