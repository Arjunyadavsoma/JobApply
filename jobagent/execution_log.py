"""Phase 19E — structured browser-execution logging.

Every automation step is recorded with: job id, session (application) id,
browser worker id, ATS platform, current step, timestamp, action result,
error classification and recovery action.

Hard privacy rule: credentials, cookies, OAuth tokens and full sensitive
form values are NEVER written. Free-text payloads pass through
``security.redact_secrets`` before storage.
"""
from __future__ import annotations

import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from jobagent import db
from jobagent.security import redact_secrets

# Error taxonomy for automation failures (stable codes, UI can group on them).
ERROR_CLASSES = {
    "navigation_timeout": "page did not load in time",
    "selector_not_found": "expected element missing from the page",
    "action_timeout": "click/fill did not complete in time",
    "form_validation": "platform rejected a field value",
    "upload_failed": "file upload rejected or failed",
    "session_expired": "platform logged the session out",
    "browser_crash": "browser process died",
    "network_error": "network dropped mid-action",
    "captcha_wall": "CAPTCHA/anti-bot challenge appeared (never bypassed)",
    "auth_wall": "login wall appeared (never bypassed)",
    "sensitive_field": "question needs the human user (never auto-answered)",
    "page_changed": "form structure changed under us",
    "llm_error": "AI answer generation failed",
    "duplicate_submission": "a submission already exists for this job",
    "unknown": "unclassified",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS execution_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    session_id  TEXT NOT NULL DEFAULT '',
    worker_id   TEXT NOT NULL DEFAULT '',
    ats         TEXT NOT NULL DEFAULT '',
    step        INTEGER NOT NULL DEFAULT 0,
    action      TEXT NOT NULL,
    result      TEXT NOT NULL,                 -- ok | failed | blocked | skipped
    error_class TEXT,
    detail      TEXT,                          -- redacted free text
    recovery    TEXT,                          -- recovery action taken/needed
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_execlogs_job ON execution_logs(job_id, created_at);
CREATE INDEX IF NOT EXISTS idx_execlogs_error ON execution_logs(error_class);
"""

_local = threading.local()


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


def worker_id() -> str:
    """Stable per-process browser worker identifier."""
    wid = getattr(_local, "worker_id", None)
    if not wid:
        wid = f"w-{uuid.uuid4().hex[:8]}"
        _local.worker_id = wid
    return wid


def classify_exception(exc: BaseException) -> str:
    """Map a Playwright/HTTP/LLM exception to the stable error taxonomy."""
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "timeout" in name or "timeout" in msg:
        return "action_timeout"
    if "net::err" in msg or "connection" in msg or "network" in msg:
        return "network_error"
    if "target closed" in msg or "browser has been closed" in msg or "crash" in msg:
        return "browser_crash"
    if "selector" in name or "waiting for locator" in msg or "strict mode violation" in msg:
        return "selector_not_found"
    return "unknown"


def log_action(*, job_id: str, action: str, result: str, session_id: str = "",
               ats: str = "", step: int = 0, error_class: str | None = None,
               detail: str = "", recovery: str = "") -> None:
    """Append one structured execution record. Never raises, never leaks."""
    if result not in ("ok", "failed", "blocked", "skipped"):
        result = "failed" if result else "ok"
    if error_class and error_class not in ERROR_CLASSES:
        error_class = "unknown"
    try:
        with db.connect() as conn:
            ensure_schema(conn)
            conn.execute(
                "INSERT INTO execution_logs (job_id, session_id, worker_id, ats, "
                "step, action, result, error_class, detail, recovery, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (job_id, session_id or "", worker_id(), ats or "", int(step),
                 action[:80], result, error_class,
                 redact_secrets(detail or "")[:500],
                 redact_secrets(recovery or "")[:200],
                 datetime.now(timezone.utc).isoformat(timespec="milliseconds")))
            conn.commit()
    except Exception:  # noqa: BLE001
        # Execution logging must never break the automation itself — any
        # storage failure (DB down, disk full, schema issue) is swallowed.
        pass


@contextmanager
def logged_action(*, job_id: str, action: str, session_id: str = "",
                  ats: str = "", step: int = 0,
                  recovery: str = "") -> Iterator[None]:
    """Context manager: logs ok on clean exit, classified failure on exception."""
    try:
        yield
        log_action(job_id=job_id, action=action, result="ok", session_id=session_id,
                   ats=ats, step=step)
    except BaseException as exc:  # noqa: BLE001 — log then re-raise
        log_action(job_id=job_id, action=action, result="failed",
                   session_id=session_id, ats=ats, step=step,
                   error_class=classify_exception(exc),
                   detail=f"{type(exc).__name__}: {exc}",
                   recovery=recovery)
        raise


def logs_for_job(job_id: str, limit: int = 200) -> list[dict[str, Any]]:
    """Structured history for one job (dashboard + audit)."""
    with db.connect() as conn:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT session_id, worker_id, ats, step, action, result, "
            "error_class, detail, recovery, created_at FROM execution_logs "
            "WHERE job_id=? ORDER BY id DESC LIMIT ?",
            (job_id, limit)).fetchall()
        return [dict(r) for r in rows]


def error_summary(limit_days: int = 7) -> dict[str, int]:
    """Failure counts by error class — feeds /api/system/diagnostics."""
    since = datetime.now(timezone.utc).timestamp() - limit_days * 86400
    since_iso = datetime.fromtimestamp(since, tz=timezone.utc).isoformat()
    with db.connect() as conn:
        ensure_schema(conn)
        try:
            rows = conn.execute(
                "SELECT error_class, COUNT(*) c FROM execution_logs "
                "WHERE result='failed' AND created_at >= ? AND error_class IS NOT NULL "
                "GROUP BY error_class ORDER BY c DESC", (since_iso,)).fetchall()
            return {r["error_class"]: r["c"] for r in rows}
        except sqlite3.Error:
            return {}
