"""Phase 19J — health checks and system diagnostics.

Endpoints served by server.py:
  /health/live      — liveness: process is up (no dependency checks)
  /health/ready     — readiness: DB reachable + schema present
  /health           — the existing public health summary (kept for the UI)
  /api/system/status      — counts, versions, background workers
  /api/system/diagnostics — deep checks: DB integrity, migrations, Groq
                            connectivity, browser availability, storage.

Nothing here ever returns secrets, file paths of the host, or environment
values — diagnostics use redacted booleans and safe summaries.
"""
from __future__ import annotations

import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jobagent import db
from jobagent.config import DATA_DIR, ROOT

STARTED_AT = time.time()
VERSION_FILE = ROOT / "VERSION"


def app_version() -> str:
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text().strip()
    return "dev"


def uptime_seconds() -> float:
    return round(time.time() - STARTED_AT, 1)


# ---------------------------------------------------------------- checks

def check_database() -> dict[str, Any]:
    """DB reachable + core tables present."""
    started = time.time()
    try:
        with db.connect() as conn:
            tables = {r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            required = {"jobs", "submissions", "job_events", "credentials"}
            missing = required - tables
            job_count = conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
        return {
            "ok": not missing,
            "detail": "ok" if not missing else f"missing tables: {sorted(missing)}",
            "jobs": job_count,
            "latency_ms": round((time.time() - started) * 1000, 1),
        }
    except sqlite3.DatabaseError as exc:
        return {"ok": False, "detail": type(exc).__name__, "jobs": 0,
                "latency_ms": round((time.time() - started) * 1000, 1)}


def check_schema_version() -> dict[str, Any]:
    """Explicit schema version (Phase 19B) vs. the code's expected version."""
    try:
        with db.connect() as conn:
            row = conn.execute("PRAGMA user_version").fetchone()
            version = row[0] if row else 0
        return {"ok": version <= db.SCHEMA_VERSION,
                "schema_version": version, "expected_max": db.SCHEMA_VERSION,
                "detail": "ok" if version <= db.SCHEMA_VERSION
                else "database is newer than this build — update the app"}
    except sqlite3.DatabaseError as exc:
        return {"ok": False, "schema_version": None, "expected_max": db.SCHEMA_VERSION,
                "detail": type(exc).__name__}


def check_db_integrity() -> dict[str, Any]:
    """PRAGMA quick_check on the live DB (fast variant of integrity_check)."""
    try:
        conn = sqlite3.connect(f"file:{db.DB_PATH}?mode=ro", uri=True, timeout=5)
        try:
            row = conn.execute("PRAGMA quick_check(1)").fetchone()
            result = str(row[0]).lower() if row else "unknown"
            return {"ok": result == "ok", "detail": result}
        finally:
            conn.close()
    except sqlite3.DatabaseError as exc:
        return {"ok": False, "detail": type(exc).__name__}


def check_groq() -> dict[str, Any]:
    """Groq connectivity — only when a key is configured (redacted result)."""
    from jobagent.security import redact_secrets
    import os
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        try:
            from jobagent import credentials
            key = credentials.resolve_api_key("GROQ_API_KEY", "groq-main") or ""
        except Exception:  # noqa: BLE001 — vault optional here
            key = ""
    if not key:
        return {"ok": False, "configured": False,
                "detail": "no Groq API key configured"}
    try:
        import httpx
        started = time.time()
        with httpx.Client(timeout=8) as http:
            resp = http.get("https://api.groq.com/openai/v1/models",
                            headers={"Authorization": f"Bearer {key}"})
        return {"ok": resp.status_code == 200, "configured": True,
                "detail": f"HTTP {resp.status_code}",
                "latency_ms": round((time.time() - started) * 1000, 1)}
    except Exception as exc:  # noqa: BLE001 — network errors expected
        return {"ok": False, "configured": True,
                "detail": redact_secrets(type(exc).__name__)}


def check_browser() -> dict[str, Any]:
    """Playwright/Chromium availability (no launch — just the binary check)."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            path = p.chromium.executable_path
            available = bool(path) and Path(path).exists()
            return {"ok": available,
                    "detail": "chromium available" if available
                    else "chromium not found — run `playwright install chromium`"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": type(exc).__name__}


def check_storage() -> dict[str, Any]:
    """Free disk space under the data dir; warns under 1 GB."""
    try:
        usage = shutil.disk_usage(DATA_DIR if DATA_DIR.exists() else ROOT)
        free_gb = usage.free / (1 << 30)
        return {"ok": free_gb > 1.0, "free_gb": round(free_gb, 1),
                "detail": "ok" if free_gb > 1.0 else "low disk space"}
    except OSError:
        return {"ok": False, "free_gb": None, "detail": "cannot stat storage"}


def check_background_workers() -> dict[str, Any]:
    """Background task loop health (tasks.py)."""
    try:
        from jobagent import tasks
        running = getattr(tasks, "_worker_running", False)
        last = getattr(tasks, "_last_tick_at", None)
        return {"ok": True, "worker_running": bool(running),
                "last_tick_at": last}
    except Exception:  # noqa: BLE001
        return {"ok": False, "worker_running": False, "last_tick_at": None}


# ---------------------------------------------------------------- summaries

def liveness() -> dict[str, Any]:
    return {"status": "ok", "service": "jobagent", "version": app_version(),
            "uptime_s": uptime_seconds(),
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def readiness() -> dict[str, Any]:
    db_check = check_database()
    return {
        "status": "ok" if db_check["ok"] else "degraded",
        "service": "jobagent",
        "version": app_version(),
        "database": db_check,
    }


def system_status() -> dict[str, Any]:
    """Lightweight operational snapshot for the dashboard."""
    with db.connect() as conn:
        jobs = conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
        applied = conn.execute(
            "SELECT COUNT(*) c FROM jobs WHERE status IN "
            "('applied','assessment','interview','offer')").fetchone()["c"]
        subs = conn.execute("SELECT COUNT(*) c FROM submissions").fetchone()["c"]
        ai_calls = conn.execute("SELECT COUNT(*) c FROM ai_usage").fetchone()["c"]
        # running background tasks: started but not ended
        pending_tasks = conn.execute(
            "SELECT COUNT(*) c FROM task_history WHERE ended_at IS NULL"
        ).fetchone()["c"]
    return {
        "version": app_version(),
        "uptime_s": uptime_seconds(),
        "jobs_total": jobs,
        "jobs_applied_pipeline": applied,
        "submissions": subs,
        "ai_calls": ai_calls,
        "background_tasks_running": pending_tasks,
        "database": check_database(),
        "storage": check_storage(),
    }


def diagnostics() -> dict[str, Any]:
    """Deep diagnostics — every check with an honest ok/not-ok, no invention."""
    checks = {
        "database": check_database(),
        "schema_version": check_schema_version(),
        "db_integrity": check_db_integrity(),
        "groq": check_groq(),
        "browser": check_browser(),
        "storage": check_storage(),
        "background_workers": check_background_workers(),
    }
    critical = ("database", "db_integrity", "schema_version")
    overall = "ok" if all(checks[k]["ok"] for k in critical) else "degraded"
    return {
        "status": overall,
        "version": app_version(),
        "uptime_s": uptime_seconds(),
        "checks": checks,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
