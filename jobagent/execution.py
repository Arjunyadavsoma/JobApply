"""Phase 17.6 — application execution with three explicit modes.

MODE A — DISCOVERY: search, verify, evaluate eligibility, rank, shortlist.
          Pure read-only research; nothing is submitted anywhere.

MODE B — ASSISTED (default): tailor resume + cover letter, answer screening
          questions, open the application in a visible browser, fill every
          answerable field, validate, then PAUSE for final human review.
          The human presses Submit (or uses 'Mark applied').

MODE C — AUTHORIZED SUBMISSION (off by default): after explicit per-job approval
          AND automation.authorized_submission=true, submit on the user's behalf
          with multiple pre-flight checks, stop on anything unexpected (CAPTCHA,
          legal declarations, sensitive questions, login walls), and capture
          confirmation evidence. A job is marked submitted ONLY when the platform
          confirms it; otherwise the state stays needs_manual with the reason.

Nothing here ever: bypasses CAPTCHA/MFA/bot protection, pays application fees,
submits on unapproved jobs, or invents a confirmation.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from jobagent import db
from jobagent.config import cfg
from jobagent.tasks import start as start_task

SENSITIVE_PATTERNS = (
    r"\baadhaar?\b", r"\bpan card\b", r"\bpassport (number|no)\b", r"\bssn\b",
    r"\bdriving licen[cs]e (number|no)\b", r"\bvoter id\b",
    r"\bi (authorize|authorise|consent|agree)\b", r"\bbackground check\b",
    r"\bcriminal (record|history)\b", r"\bdrug test\b", r"\bnon[- ]?compete\b",
    r"\bdeclaration\b", r"\bsignature\b", r"\be[- ]?sign\b",
    r"\bterms (and|&) conditions\b", r"\bprivacy policy\b", r"\blikeness\b",
    r"\bwork authori[sz]ation\b", r"\bvisa (status|sponsorship)\b",
    r"\breligion\b", r"\bcaste\b", r"\bmarital status\b", r"\bgender\b",
    r"\bdisability\b", r"\bsalary (history|expectations?)\b",
)

CONFIRM_MARKERS = (
    "thank you for applying", "application received", "application has been submitted",
    "successfully submitted", "application submitted", "we have received your application",
    "your application has been received", "applied successfully",
    "confirmation", "check your email",
)


def sensitive_fields(fields: list[dict]) -> list[str]:
    """Field labels that require a human decision (IDs, legal, demographic...)."""
    hits = []
    for f in fields:
        label = (f.get("label") or "").lower()
        if any(re.search(p, label) for p in SENSITIVE_PATTERNS):
            hits.append(f.get("label") or f.get("name") or "?")
    return hits


def captcha_present(page) -> bool:
    """Detect (never solve) common bot-protection widgets."""
    for sel in ("iframe[src*='recaptcha']", "iframe[title*='recaptcha']",
                "iframe[src*='hcaptcha']", ".h-captcha", "[data-sitekey]",
                "iframe[src*='turnstile']", ".cf-turnstile"):
        try:
            if page.locator(sel).count() > 0:
                return True
        except Exception:                                    # noqa: BLE001
            continue
    return False


# ================================================================ MODE A: discovery

def mode_a(search_only: str | None = None) -> dict[str, Any]:
    """Discovery run: discover -> verify -> score -> shortlist-eligible. Returns stats."""
    from jobagent.sources import discover
    inserted = discover(only=search_only)
    from jobagent import prefit
    prefit.update()
    # score top new jobs (LLM) - the queue's analyze action does per-job deep checks
    scored = 0
    try:
        from jobagent.resume import load_master
        from jobagent.matcher import score_new
        score_new(load_master(), limit=cfg("search.autoscore_limit", 10))
        scored = 1
    except SystemExit:                                       # no master resume yet
        scored = 0
    except Exception as e:                                   # noqa: BLE001
        db.add_notification("error", "Scoring failed", str(e)[:200])
    stats = db.stats()
    return {"inserted": inserted, "scoring": "ran" if scored else "skipped (no resume?)",
            "pipeline": stats}


# ================================================================ MODE B: assisted

def mode_b(job_id: str, tailor_first: bool = True) -> dict[str, Any]:
    """Assisted application: launch the human-in-the-loop browser session.

    Runs as a background task (tasks.start) exactly like the dashboard's Apply
    button, with a persisted apply-session so an interrupted run can resume.
    """
    job = db.get(job_id)
    if not job:
        raise KeyError(job_id)
    if not cfg("automation", {}).get("assisted_enabled", True):
        return {"ok": False, "error": "assisted mode disabled"}
    db.save_session(job_id, "assisted", {"step": "starting", "tailor": tailor_first,
                                         "url": job["apply_url"]})
    db.update(job_id, apply_mode="assisted")
    from jobagent.queue import set_queue_state
    set_queue_state(job_id, "applying", "assisted session opening")
    args = ["apply-one", job_id] + (["--tailor"] if tailor_first else [])
    t = start_task(f"Assisted apply: {job['company']} - {job['title']}", *args)
    return {"ok": True, "mode": "assisted", "task": t}


def record_manual_submission(job_id: str, note: str = "") -> dict[str, Any]:
    """The human submitted the form themselves; record the evidence honestly."""
    job = db.get(job_id)
    if not job:
        raise KeyError(job_id)
    evidence = {"note": note or "user confirmed manual submission",
                "final_url": job["apply_url"]}
    db.record_submission(job_id, "manual", evidence, confirmed=True)
    db.update(job_id, status="applied", applied_at=db.now())
    from jobagent.queue import set_queue_state
    set_queue_state(job_id, "submitted", "manual submission confirmed by user")
    db.close_session(job_id, "completed")
    return {"ok": True}


# ================================================================ MODE C: authorized

def authorized_allowed() -> tuple[bool, str]:
    if not cfg("automation.authorized_submission", False):
        return False, ("Authorized submission is OFF. Enable automation.authorized_submission "
                       "in config.yaml and approve the job first - deliberately not a one-click "
                       "switch in the UI.")
    return True, ""


def mode_c_preflight(job_id: str) -> dict[str, Any]:
    """Everything checked BEFORE an authorized submit is allowed to proceed."""
    ok_flag, why = authorized_allowed()
    if not ok_flag:
        return {"ok": False, "error": why}
    from jobagent import queue as q
    job = db.get(job_id)
    if not job:
        return {"ok": False, "error": "job not found"}
    if (job["queue_state"] or "") != "awaiting_approval":
        return {"ok": False, "error": f"job is {job['queue_state'] or 'new'} - needs explicit "
                                      "approval (queue action 'approve') first"}
    gates, reason = q.can_apply(job_id)
    if not gates:
        return {"ok": False, "error": reason}
    if not job["resume_path"] or not Path(job["resume_path"]).exists():
        return {"ok": False, "error": "no tailored resume - run 'prepare' first"}
    return {"ok": True, "job": job}


def mode_c(job_id: str) -> dict[str, Any]:
    """Authorized submission. Only runs when every gate passes; never bypasses protection.

    The submit step runs in a visible browser session (same trust model as assisted):
    it fills, validates, and clicks Submit ONLY when the preflight passed, no
    CAPTCHA is present, and no sensitive/legal fields remain unanswered. Any
    surprise stops the session with state needs_manual + a screenshot.
    """
    pre = mode_c_preflight(job_id)
    if not pre.get("ok"):
        return pre
    job = pre["job"]
    db.save_session(job_id, "authorized", {"step": "authorized-submit-start",
                                           "url": job["apply_url"]})
    db.update(job_id, apply_mode="authorized")
    from jobagent.queue import set_queue_state
    set_queue_state(job_id, "applying", "authorized submission attempt")
    t = start_task(f"Authorized submit: {job['company']} - {job['title']}",
                   "submit-authorized", job_id)
    return {"ok": True, "mode": "authorized", "task": t}


CONFIRM_RE = re.compile("|".join(re.escape(m) for m in CONFIRM_MARKERS), re.I)


def confirm_from_text(text: str) -> str | None:
    """Return the confirmation phrase found in the page, or None."""
    if not text:
        return None
    m = CONFIRM_RE.search(text)
    return m.group(0) if m else None


def record_authorized_result(job_id: str, *, confirmed: bool, final_url: str = "",
                             screenshot: str = "", page_text: str = "",
                             stopped_reason: str = "") -> dict[str, Any]:
    """Persist the outcome of an authorized attempt with evidence."""
    job = db.get(job_id)
    evidence = {
        "final_url": final_url, "screenshot": screenshot,
        "confirmation_text": confirm_from_text(page_text),
        "stopped_reason": stopped_reason or None,
        "recorded_at": db.now(),
    }
    if confirmed:
        db.record_submission(job_id, "authorized", evidence, confirmed=True)
        db.update(job_id, status="applied", applied_at=db.now())
        from jobagent.queue import set_queue_state
        set_queue_state(job_id, "submitted", "platform confirmed submission")
        db.close_session(job_id, "completed")
        db.add_notification("status", f"Submitted: {job['company']} - {job['title']}",
                            "Authorized submission confirmed by the platform.", job_id)
    else:
        from jobagent.queue import set_queue_state
        set_queue_state(job_id, "needs_manual",
                        stopped_reason or "no platform confirmation captured")
        db.close_session(job_id, "needs_human")
    return {"ok": confirmed, "evidence": evidence}
