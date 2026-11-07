"""HTTP API for the React dashboard (web/). Serves the built app from web/dist too.

Run: python -m jobagent web   (or the platform launcher)
Long operations run as background tasks (see tasks.py); the UI polls /api/task for live progress.

Security model (single-user local-first tool):
- Binds 127.0.0.1 by default. Set DASHBOARD_TOKEN to require X-Dashboard-Token on /api/*
  (e.g. when forwarding the port or running on a shared host). /api/health stays open.
- Uploads: PDF-only, 10 MB max. Job text is treated as untrusted data everywhere it reaches AI.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from ruamel.yaml import YAML

from jobagent import analytics, db, tasks
from jobagent.config import BASE_RESUME_PDF, CONFIG_PATH, MASTER_RESUME_JSON, PROFILE_DIR, ROOT, cfg, load_config

app = FastAPI(title="JobApply - AI job application platform")
DASHBOARD_TOKEN = os.environ.get("DASHBOARD_TOKEN", "").strip()
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

WINDOWS = sys.platform.startswith("win")


@app.on_event("startup")
def _startup() -> None:
    from jobagent import prefit
    prefit.update()                    # jobs found before the quick fit estimate existed
    _notify_deadlines()
    tasks.start_auto_discovery()       # no-op unless search.autodiscover_hours is set


def _notify_deadlines() -> None:
    """In-app notifications for jobs whose application deadline is within 3 days."""
    for row in analytics.upcoming_deadlines(3):
        with db.connect() as conn:     # one notification per job per day
            seen = conn.execute(
                "SELECT 1 FROM notifications WHERE job_id = ? AND kind = 'deadline'"
                " AND created_at LIKE ?", (row["id"], f"{db.now()[:10]}%"),
            ).fetchone()
        if not seen:
            db.add_notification("deadline", f"Deadline {row['deadline']}: {row['company']}",
                                f"{row['title']} - apply before {row['deadline']}", row["id"])


@app.middleware("http")
async def _auth(request: Request, call_next):
    if DASHBOARD_TOKEN and request.url.path.startswith("/api") and request.url.path != "/api/health":
        if request.headers.get("X-Dashboard-Token") != DASHBOARD_TOKEN:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
    return await call_next(request)


yaml = YAML()  # round-trip: keeps the comments in config.yaml
yaml.width = 120

STATUSES = db.PIPELINE
LIST_FIELDS = ("id", "source", "company", "title", "location", "status", "score", "prefit", "url",
               "apply_url", "posted_at", "discovered_at", "applied_at", "salary_min", "salary_max",
               "salary_text", "stipend", "employment_type", "remote_type", "experience_min",
               "experience_max", "batch_years", "deadline", "fresher_flag", "walk_in", "off_campus",
               "verified", "fraud_flags", "notes", "min_cgpa", "min_percentage",
               "verification_state", "verification_note", "quality_score", "queue_state",
               "queue_ready", "apply_mode", "last_verified_at")


def _job_summary(r) -> dict[str, Any]:
    d = {k: r[k] for k in LIST_FIELDS if k in r.keys()}
    d["has_resume"] = bool(r["resume_path"]) and Path(r["resume_path"]).exists()
    for k in ("batch_years", "fraud_flags"):
        if isinstance(d.get(k), str) and d[k].strip():
            try:
                d[k] = json.loads(d[k])
            except ValueError:
                d[k] = []
        elif d.get(k) is None:
            d[k] = []
    return d


def _require(job_id: str):
    job = db.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


def _start(label: str, *args: str) -> dict:
    try:
        return tasks.start(label, *args)
    except RuntimeError as e:
        raise HTTPException(409, str(e))


def _open_path(path: Path) -> None:
    """Cross-platform "open in file manager"."""
    try:
        if WINDOWS:
            os.startfile(str(path))                                    # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except OSError:
        pass


# ---------------------------------------------------------------- health + stats

@app.get("/health/live")
def health_live() -> dict:
    """Phase 19J: liveness — process is up, no dependency checks."""
    from jobagent.health import liveness
    return liveness()


@app.get("/health/ready")
def health_ready() -> dict:
    """Phase 19J: readiness — DB reachable, schema present."""
    from jobagent.health import readiness
    out = readiness()
    if out["status"] != "ok":
        from fastapi.responses import JSONResponse
        return JSONResponse(out, status_code=503)
    return out


@app.get("/api/system/status")
def system_status() -> dict:
    """Phase 19J: operational snapshot for the dashboard."""
    from jobagent.health import system_status
    return system_status()


@app.get("/api/system/diagnostics")
def system_diagnostics() -> dict:
    """Phase 19J: deep diagnostics (DB integrity, Groq, browser, storage)."""
    from jobagent.health import diagnostics
    return diagnostics()


@app.get("/api/health")
def health() -> dict:
    """Phase 17.11: liveness + dependency report. The dashboard stays usable when
    Groq or a source is down - this endpoint shows what is degraded, not just up/down."""
    from jobagent.sources import source_health
    from jobagent import groq_keys
    from jobagent import llm_providers
    llm_backend = cfg("llm.backend", "")
    pool_state = groq_keys.pool_status()
    fallback_chain = llm_providers.chain()
    sources = source_health()
    degraded = [s for s, v in sources.items()
                if v.get("last_error") and str(v.get("last_run")) >= db.now()[:10]]
    return {
        "ok": True,
        "status": "ok",
        "time": db.now(),
        "llm": {"backend": llm_backend,
                "groq_key_present": pool_state["total"] > 0,
                "groq_keys": {"total": pool_state["total"],
                              "active": pool_state["active"],
                              "cooling": pool_state["cooling"],
                              "disabled": pool_state["disabled"]},
                "fallbacks": {"configured": len(fallback_chain),
                              "names": [s.name for s in fallback_chain]}},
        "db": {"jobs": db.stats()["total"], "writable": True},
        "sources": {"tracked": len(sources), "degraded_today": degraded},
        "task_running": bool(tasks.current() and tasks.current().get("running")),
        "version": "20.0",
    }


@app.get("/api/stats")
def stats() -> dict:
    s = db.stats()
    s["has_resume"] = MASTER_RESUME_JSON.exists()
    s["has_profile"] = (PROFILE_DIR / "candidate_profile.json").exists()
    # Phase 17: verification + queue breakdowns from persisted data
    with db.connect() as conn:
        for key, sql in (("verification", "SELECT verification_state k, COUNT(*) c FROM jobs GROUP BY verification_state"),
                         ("queue", "SELECT queue_state k, COUNT(*) c FROM jobs GROUP BY queue_state")):
            s[key] = {r["k"] or "unverified": r["c"] for r in conn.execute(sql)}
        today = db.now()[:10]
        row = conn.execute("SELECT COUNT(*) c FROM submissions WHERE submitted_at LIKE ?",
                           (f"{today}%",)).fetchone()
        s["submissions_today"] = row["c"]
    return s


# ---------------------------------------------------------------- jobs (filtered list + India views)

@app.get("/api/jobs")
def list_jobs(status: str = "", source: str = "", employment_type: str = "", remote_type: str = "",
              fresher: int = 0, walk_in: int = 0, off_campus: int = 0, batch_year: int = 0,
              search: str = "", min_score: int = 0, deadline_days: int = 0, limit: int = 300,
              offset: int = 0, location: str = "", min_salary: float = 0, skills: str = "",
              max_experience: float = -1, verification_state: str = "", queue_state: str = "") -> list[dict]:
    filters = {k: v for k, v in {"status": status, "source": source,
                                 "employment_type": employment_type,
                                 "remote_type": remote_type, "fresher_flag": fresher or None,
                                 "walk_in": walk_in or None, "off_campus": off_campus or None,
                                 "verification_state": verification_state,
                                 "queue_state": queue_state}.items() if v}
    rows = db.query_jobs(filters, search=search or "", min_score=min_score or None,
                         batch_year=batch_year or None,
                         deadline_within_days=deadline_days or None,
                         location=location or "", min_salary=min_salary or None,
                         skills=skills or "",
                         max_experience=(None if max_experience < 0 else max_experience),
                         limit=min(limit, 1000), offset=max(offset, 0))
    return [_job_summary(r) for r in rows]


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str) -> dict:
    r = _require(job_id)
    d = _job_summary(r)
    d["description"] = r["description"]
    d["error"] = r["error"]
    d["match"] = json.loads(r["match_json"]) if r["match_json"] else None
    d["changes"] = json.loads(r["changes_json"]) if r["changes_json"] else []
    d["eligibility"] = (d["match"] or {}).get("eligibility") if d["match"] else None
    cover = Path(r["cover_path"]) if r["cover_path"] else None
    d["cover_letter"] = cover.read_text(encoding="utf-8") if cover and cover.exists() else None
    d["resume_file"] = Path(r["resume_path"]).name if d["has_resume"] else None
    d["events"] = [dict(e) for e in db.events_for(job_id, 50)]
    return d


class StatusBody(BaseModel):
    status: str
    note: str = ""


@app.post("/api/jobs/{job_id}/status")
def set_status(job_id: str, body: StatusBody) -> dict:
    job = _require(job_id)
    # Guard: never mark applied twice (duplicate applications are the classic job-hunt mistake).
    if body.status == "applied" and job["status"] in ("applied", "assessment", "interview", "offer"):
        raise HTTPException(409, "Already applied to this job")
    if not db.set_status(job_id, body.status, body.note):
        raise HTTPException(400, f"Unknown status '{body.status}'")
    if body.status in ("interview", "offer"):
        db.add_notification("status", f"{body.status.title()}: {job['company']}", job["title"], job_id)
    return {"ok": True}


class NotesBody(BaseModel):
    notes: str


@app.post("/api/jobs/{job_id}/notes")
def set_notes(job_id: str, body: NotesBody) -> dict:
    _require(job_id)
    db.update(job_id, notes=body.notes)
    db.record_event(job_id, "note", (body.notes or "")[:300])
    return {"ok": True}


@app.post("/api/jobs/{job_id}/verify")
def verify_job(job_id: str) -> dict:
    """Phase 17.4: full verification battery (HTTP + content + domain + freshness)."""
    from jobagent import verify as verify_engine
    _require(job_id)
    try:
        res = verify_engine.verify_job(job_id)
    except Exception as e:                                   # noqa: BLE001
        raise HTTPException(502, f"Verification failed: {e}") from e
    return {"ok": res.state in ("verified_open", "probably_open"),
            "state": res.state, "note": res.note,
            "quality_score": res.quality_score,
            "components": res.components,
            "final_url": res.final_url,
            "fraud_flags": res.fraud_flags}


@app.post("/api/jobs/{job_id}/mark-applied")
def mark_applied(job_id: str, body: NotesBody | None = None) -> dict:
    """The user submitted the form themselves; record honest evidence (Phase 17.6)."""
    from jobagent import execution
    _require(job_id)
    return execution.record_manual_submission(job_id, (body.notes if body else "") or "")


# ---------------------------------------------------------------- Phase 17: queue

@app.get("/api/queue")
def get_queue(limit: int = 100, states: str = "") -> dict:
    from jobagent import queue as Q
    state_list = [s for s in states.split(",") if s] or None
    return {"rows": Q.queue_rows(limit=min(limit, 500), states=state_list),
            "limits": Q.limits(),
            "daily_used": Q.daily_submissions()}


@app.post("/api/queue/{job_id}/action/{action}")
def queue_action(job_id: str, action: str) -> dict:
    from jobagent import queue as Q
    _require(job_id)
    try:
        return Q.run_action(action, job_id)
    except KeyError as e:
        raise HTTPException(400, str(e)) from e


class ApplyModeBody(BaseModel):
    mode: str = "assisted"          # assisted | authorized
    tailor: bool = True


@app.post("/api/queue/{job_id}/apply")
def queue_apply(job_id: str, body: ApplyModeBody) -> dict:
    """Phase 17.6: launch MODE B (assisted) or MODE C (authorized) execution."""
    from jobagent import execution
    _require(job_id)
    try:
        if body.mode == "authorized":
            return execution.mode_c(job_id)
        if body.mode == "assisted":
            return execution.mode_b(job_id, tailor_first=body.tailor)
        raise HTTPException(400, f"Unknown mode '{body.mode}' (assisted|authorized)")
    except RuntimeError as e:                                  # task spawn conflict
        raise HTTPException(409, str(e)) from e


# ---------------------------------------------------------------- Phase 17: agents

@app.get("/api/agents")
def list_agents() -> list[dict]:
    from jobagent import agents
    return agents.registry()


@app.post("/api/agents/{agent}/jobs/{job_id}")
def run_agent(agent: str, job_id: str, body: NotesBody | None = None) -> dict:
    from jobagent import agents
    _require(job_id)
    try:
        return agents.run(agent, job_id, question=(body.notes if body else None))
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:                                     # noqa: BLE001 - LLMError etc.
        raise HTTPException(502, f"Agent failed: {e}") from e


# ---------------------------------------------------------------- Phase 17: search profiles + sources

@app.get("/api/search-profiles")
def get_search_profiles() -> dict:
    from jobagent import search_profiles as SP
    return {"profiles": SP.profiles(), "active": SP.active(),
            "quick_views": SP.quick_views()}


class ProfileBody(BaseModel):
    profile: dict[str, Any]


@app.put("/api/search-profiles")
def put_search_profile(body: ProfileBody) -> dict:
    from jobagent import search_profiles as SP
    if not body.profile.get("name"):
        raise HTTPException(400, "Profile needs a name")
    SP.save(body.profile)
    return {"ok": True, "profiles": SP.profiles()}


@app.post("/api/search-profiles/{name}/apply")
def apply_search_profile(name: str) -> dict:
    from jobagent import search_profiles as SP
    try:
        return {"ok": True, "profile": SP.apply(name)}
    except KeyError as e:
        raise HTTPException(404, str(e)) from e


@app.get("/api/sources/external")
def external_sources() -> list[dict]:
    """India platforms with no legitimate programmatic access: deep links + honest reasons."""
    from jobagent.sources import external
    return external.links_for(cfg("search.keywords", []))


@app.get("/api/ats/detect")
def ats_detect(url: str) -> dict:
    from jobagent import ats_registry
    return ats_registry.detect(url)


@app.get("/api/ats/platforms")
def ats_platforms() -> list[dict]:
    from jobagent import ats_registry
    return ats_registry.known_platforms()


@app.get("/api/ats/matrix")
def ats_matrix() -> list[dict]:
    """Phase 18: tested vs best-effort platform compatibility."""
    from jobagent.ats_flow import compatibility_matrix
    return compatibility_matrix()


# ---------------------------------------------------------------- Phase 18: application execution

@app.get("/api/jobs/{job_id}/readiness")
def job_readiness(job_id: str) -> dict:
    from jobagent import readiness
    try:
        return readiness.score(job_id)
    except KeyError as e:
        raise HTTPException(404, "job not found") from e


@app.post("/api/jobs/{job_id}/prepare")
def job_prepare(job_id: str) -> dict:
    """Run the full 13-step preparation workflow (runs inline; LLM steps degrade)."""
    from jobagent import prep
    try:
        return prep.prepare(job_id)
    except KeyError as e:
        raise HTTPException(404, "job not found") from e
    except Exception as e:                                     # noqa: BLE001
        raise HTTPException(502, f"preparation failed: {e}") from e


@app.get("/api/jobs/{job_id}/preview")
def job_preview(job_id: str) -> dict:
    from jobagent import prep
    try:
        return prep.preview(job_id)
    except KeyError as e:
        raise HTTPException(404, "job not found") from e


@app.get("/api/jobs/{job_id}/company-intel")
def job_company_intel(job_id: str, refresh: bool = False) -> dict:
    from jobagent import company_intel
    try:
        if refresh:
            return company_intel.research(job_id, force=True)
        cached = company_intel.get(job_id)
        if cached:
            return cached
        return company_intel.research(job_id)
    except KeyError as e:
        raise HTTPException(404, "job not found") from e
    except Exception as e:                                     # noqa: BLE001
        raise HTTPException(502, f"company research failed: {e}") from e


class BatchBody(BaseModel):
    job_ids: list[str]
    label: str = ""
    allowed_companies: list[str] | None = None
    excluded_companies: list[str] | None = None
    allowed_locations: list[str] | None = None
    allowed_job_types: list[str] | None = None
    max_batch_size: int | None = None


@app.post("/api/batch/prepare")
def batch_prepare(body: BatchBody) -> dict:
    from jobagent import batch
    controls = {k: v for k, v in body.model_dump().items()
                if k not in ("job_ids", "label") and v is not None}
    try:
        return batch.batch_prepare(body.job_ids, label=body.label, controls=controls or None)
    except Exception as e:                                     # noqa: BLE001
        raise HTTPException(502, f"batch prepare failed: {e}") from e


@app.get("/api/batch/history")
def batch_history(limit: int = 20) -> list[dict]:
    from jobagent import batch
    return batch.batch_history(limit=min(limit, 100))


@app.get("/api/jobs/{job_id}/interview-prep")
def interview_prep_plan(job_id: str) -> dict:
    from jobagent import interview_prep as ip
    try:
        plan = db.get_interview_prep(job_id) or ip.plan(job_id)
        return {"plan": plan, "progress": ip.progress(job_id)}
    except KeyError as e:
        raise HTTPException(404, "job not found") from e
    except Exception as e:                                     # noqa: BLE001
        raise HTTPException(502, f"interview prep failed: {e}") from e


class MockBody(BaseModel):
    category: str = ""


@app.post("/api/jobs/{job_id}/interview-prep/mock")
def interview_prep_mock(job_id: str, body: MockBody | None = None) -> dict:
    """Next mock question (optional category filter in the body)."""
    from jobagent import interview_prep as ip
    category = (body.category or "") if body else ""
    q = ip.mock_question(job_id, category=category)
    if not q:
        raise HTTPException(404, "no prep plan yet - generate one first")
    return q


class MockAnswerBody(BaseModel):
    question_id: str
    answered: bool = True


@app.post("/api/jobs/{job_id}/interview-prep/answer")
def interview_prep_answer(job_id: str, body: MockAnswerBody) -> dict:
    from jobagent import interview_prep as ip
    try:
        return ip.mock_answer(job_id, body.question_id, answered=body.answered)
    except KeyError as e:
        raise HTTPException(404, "question not found") from e


@app.get("/api/agents/history")
def agent_history(limit: int = 100, agent: str = "", job_id: str = "") -> list[dict]:
    from jobagent import agents
    return agents.history(limit=min(limit, 500), agent=agent, job_id=job_id)


@app.post("/api/jobs/{job_id}/apply/resume-session")
def resume_apply_session(job_id: str) -> dict:
    """Recovery: reopen an interrupted application (returns the report; the
    assisted worker picks it up from there)."""
    from jobagent import recovery
    try:
        recovery.acquire(job_id, owner=f"api-{db.now()}")
    except recovery.LockHeld as e:
        raise HTTPException(409, f"another session is running: {e.owner}") from e
    if db.submissions_for(job_id):
        recovery.release(job_id)
        raise HTTPException(409, "this application was already submitted - "
                                 "duplicate submissions are blocked")
    try:
        from jobagent.browser import headless_page
        from jobagent import ats_flow
        with headless_page() as page:
            job = db.get(job_id)
            url = job["apply_url"] or job["url"]
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            report = recovery.resume(job_id, page)
            adapter, info = ats_flow.detect_adapter(page.url, page)
            report["ats"] = info.get("platform", "generic")
    except recovery.AlreadySubmitted:
        raise HTTPException(409, "already submitted")
    except Exception as e:                                     # noqa: BLE001
        recovery.release(job_id)
        raise HTTPException(502, f"recovery failed: {e}") from e
    recovery.release(job_id)
    return report


# ---------------------------------------------------------------- Phase 17: email monitoring

@app.get("/api/emails")
def get_emails(unreviewed: bool = False) -> list[dict]:
    rows = db.emails(unreviewed_only=unreviewed)
    out = []
    for r in rows:
        d = dict(r)
        d["confidence"] = round(r["confidence"] or 0, 2)
        out.append(d)
    return out


@app.get("/api/emails/status")
def emails_status() -> dict:
    from jobagent import email_sync
    return email_sync.configured()


@app.post("/api/emails/sync")
def emails_sync() -> dict:
    from jobagent import email_sync
    try:
        stats = email_sync.sync()
        return {"ok": True, **stats}
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:                                     # noqa: BLE001
        raise HTTPException(502, f"Email sync failed: {e}") from e


class EmailReviewBody(BaseModel):
    classification: str | None = None
    job_id: str | None = None


@app.post("/api/emails/{email_id}/review")
def review_email(email_id: int, body: EmailReviewBody) -> dict:
    db.review_email(email_id, classification=body.classification,
                    matched_job_id=body.job_id)
    return {"ok": True}


# ---------------------------------------------------------------- Phase 17: task history + backup

@app.get("/api/tasks")
def task_history(limit: int = 50) -> list[dict]:
    return [dict(r) for r in db.task_history(limit=min(limit, 200))]


@app.post("/api/backup")
def backup_now(label: str = "") -> dict:
    """Phase 19B: create a validated backup (integrity-checked + hashed)."""
    from jobagent.backup import BackupError, create_backup
    try:
        info = create_backup(label=label)
    except BackupError as exc:
        raise HTTPException(500, str(exc))
    return {"ok": True, **info.as_dict()}


@app.get("/api/backup")
def backup_list() -> dict:
    """Phase 19B: all backups with integrity status (newest first)."""
    from jobagent.backup import apply_retention, list_backups
    apply_retention()  # prune per retention policy on every listing
    backups = [b.as_dict() for b in list_backups()]
    return {"ok": True, "backups": backups, "count": len(backups)}


@app.get("/api/backup/{name}/verify")
def backup_verify(name: str) -> dict:
    """Phase 19B: re-validate one backup (integrity + hash)."""
    from jobagent.backup import BackupError, verify_backup
    try:
        return {"ok": True, **verify_backup(name)}
    except BackupError as exc:
        raise HTTPException(404, str(exc))


@app.post("/api/backup/{name}/restore")
def backup_restore(name: str) -> dict:
    """Phase 19B: restore a backup over the live DB.

    Destructive: covered by the token middleware when configured, and it
    always creates a pre-restore safety copy first.
    """
    from jobagent.backup import BackupError, restore_backup
    try:
        return restore_backup(name)
    except BackupError as exc:
        code = 404 if "not found" in str(exc) else 400
        raise HTTPException(code, str(exc))


# ------------------------------------------------------- credentials (19A-2)

class CredentialIn(BaseModel):
    name: str
    provider: str
    secret: str
    note: str = ""


class CredentialUpdateIn(BaseModel):
    secret: str
    note: str | None = None


def _reload_key_pool_if_groq(provider: str) -> None:
    """Vault changes must reach the rotation pools without a restart."""
    if provider == "groq":
        from jobagent import groq_keys
        groq_keys.reload_pool()
    from jobagent import llm_providers          # Phase 20: fallback pools too
    llm_providers.reload_providers()


@app.get("/api/credentials")
def credentials_list() -> list[dict]:
    """Masked credential listing — secrets never leave the vault."""
    from jobagent import credentials
    return credentials.list_credentials()


@app.post("/api/credentials")
def credentials_add(body: CredentialIn) -> dict:
    from jobagent import credentials
    try:
        out = credentials.add_credential(
            body.name, body.provider, body.secret, note=body.note)
    except credentials.VaultError as exc:
        raise HTTPException(400, str(exc))
    _reload_key_pool_if_groq(body.provider)
    return out


@app.put("/api/credentials/{name}")
def credentials_update(name: str, body: CredentialUpdateIn) -> dict:
    from jobagent import credentials
    try:
        out = credentials.update_credential(name, body.secret, note=body.note)
    except credentials.VaultError as exc:
        raise HTTPException(400, str(exc))
    _reload_key_pool_if_groq(_provider_of(name))
    return out


@app.post("/api/credentials/{name}/test")
def credentials_test(name: str) -> dict:
    from jobagent import credentials
    try:
        return credentials.test_credential(name)
    except credentials.VaultError as exc:
        raise HTTPException(404, str(exc))


@app.post("/api/credentials/{name}/revoke")
def credentials_revoke(name: str) -> dict:
    from jobagent import credentials
    provider = _provider_of(name)          # before the row is flagged
    try:
        out = credentials.revoke_credential(name)
    except credentials.VaultError as exc:
        raise HTTPException(400, str(exc))
    _reload_key_pool_if_groq(provider)
    return out


@app.delete("/api/credentials/{name}")
def credentials_delete(name: str) -> dict:
    from jobagent import credentials
    provider = _provider_of(name)          # before the row is removed
    try:
        out = credentials.delete_credential(name)
    except credentials.VaultError as exc:
        raise HTTPException(404, str(exc))
    _reload_key_pool_if_groq(provider)
    return out


def _provider_of(name: str) -> str:
    from jobagent import credentials
    try:
        for row in credentials.list_credentials():
            if row["name"] == name:
                return row["provider"]
    except Exception:  # noqa: BLE001 — best effort only
        pass
    return ""


# --------------------------------------------------- groq key rotation (19K)

@app.get("/api/llm/keys")
def llm_keys_status() -> dict:
    """Live rotation-pool status: masked keys, states, stats. No secrets."""
    from jobagent import groq_keys
    return groq_keys.pool_status()


@app.post("/api/llm/keys/reset")
def llm_keys_reset() -> dict:
    """Operator action: re-read all key sources and re-arm cooling/disabled keys."""
    from jobagent import groq_keys
    return groq_keys.reset_pool()


# --------------------------------------------- provider fallback chain (20)

@app.get("/api/llm/providers")
def llm_providers_status() -> dict:
    """Phase 20: the configured fallback chain — per-provider key pools
    (masked), breaker states, models. No secrets, ever."""
    from jobagent import llm_providers
    return llm_providers.chain_status()


@app.post("/api/llm/providers/reset")
def llm_providers_reset() -> dict:
    """Operator action: re-read the chain and re-arm every key in every pool."""
    from jobagent import llm_providers
    return llm_providers.reset_providers()


# ------------------------------------------------------- state machine (19D)

@app.get("/api/jobs/{job_id}/execution-logs")
def execution_logs(job_id: str, limit: int = 200) -> list[dict]:
    """Phase 19E: structured automation history for one job (redacted)."""
    from jobagent import execution_log
    return execution_log.logs_for_job(job_id, limit=min(limit, 500))


@app.get("/api/applications/{job_id}/state")
def application_state(job_id: str) -> dict:
    """Current formal lifecycle state + full transition history."""
    from jobagent import state_machine as sm
    return {"job_id": job_id, "state": sm.current_state(job_id),
            "history": sm.history(job_id)}


@app.get("/api/applications/states")
def application_state_counts() -> dict:
    from jobagent import state_machine as sm
    return {"ok": True, "counts": sm.state_counts(),
            "states": sorted(sm.STATES)}


class TransitionIn(BaseModel):
    to_state: str
    actor: str = "user"
    note: str = ""
    evidence: dict | None = None
    idempotency_key: str | None = None


@app.post("/api/applications/{job_id}/transition")
def application_transition(job_id: str, body: TransitionIn) -> dict:
    """Apply a lifecycle transition with full validation + audit."""
    from jobagent import state_machine as sm
    try:
        result = sm.transition(job_id, body.to_state, actor=body.actor,
                               evidence=body.evidence, note=body.note,
                               idempotency_key=body.idempotency_key)
        return {"ok": True, **result.as_dict()}
    except sm.InvalidTransition as exc:
        raise HTTPException(409, str(exc))


@app.get("/api/jobs/{job_id}/resume.pdf")
def resume_pdf(job_id: str):
    r = _require(job_id)
    if not r["resume_path"] or not Path(r["resume_path"]).exists():
        raise HTTPException(404, "No tailored resume")
    p = Path(r["resume_path"])
    return FileResponse(p, media_type="application/pdf", filename=p.name, content_disposition_type="inline")


@app.get("/api/jobs/{job_id}/cover.pdf")
def cover_pdf(job_id: str):
    r = _require(job_id)
    p = Path(r["cover_path"]).with_suffix(".pdf") if r["cover_path"] else None
    if not p or not p.exists():
        raise HTTPException(404, "No cover letter")
    return FileResponse(p, media_type="application/pdf", filename=p.name, content_disposition_type="inline")


@app.post("/api/jobs/{job_id}/open-folder")
def open_folder(job_id: str) -> dict:
    r = _require(job_id)
    if not r["resume_path"]:
        raise HTTPException(404, "No tailored resume")
    _open_path(Path(r["resume_path"]).parent)
    return {"ok": True}


# ---------------------------------------------------------------- operations (background tasks)

class SearchBody(BaseModel):
    keywords: list[str]
    locations: list[str]
    sources: list[str]            # subset of companies, linkedin, naukri, instahyre, remotive, arbeitnow
    max_age_days: int = 14
    score_limit: int = 0          # 0 = find only
    tailor_limit: int = 0


@app.post("/api/search")
def search(body: SearchBody) -> dict:
    valid = {"companies", "linkedin", "naukri", "instahyre", "remotive", "arbeitnow"}
    if not body.sources or not set(body.sources) <= valid:
        raise HTTPException(400, f"Pick at least one source from: {', '.join(sorted(valid))}")
    if {"linkedin", "naukri"} & set(body.sources) and not body.keywords:
        raise HTTPException(400, "Add at least one keyword for LinkedIn/Naukri")
    doc = _load_yaml()
    doc["search"]["keywords"] = body.keywords
    doc["search"]["locations"] = body.locations
    doc["search"]["max_age_days"] = body.max_age_days
    _save_yaml(doc)
    names = {"companies": "career sites", "linkedin": "LinkedIn", "naukri": "Naukri",
             "instahyre": "Instahyre", "remotive": "Remotive", "arbeitnow": "Arbeitnow"}
    label = "Search " + ", ".join(names.get(s, s) for s in body.sources)
    return _start(label, "search", "--sources", ",".join(body.sources),
                  "--score-limit", str(body.score_limit), "--tailor-limit", str(body.tailor_limit))


class LimitBody(BaseModel):
    limit: int = 30


@app.post("/api/score")
def score(body: LimitBody) -> dict:
    return _start(f"Score {body.limit} jobs", "score", "--limit", str(body.limit))


@app.post("/api/tailor")
def tailor(body: LimitBody) -> dict:
    return _start(f"Tailor {body.limit} resumes", "tailor", "--limit", str(body.limit))


@app.post("/api/jobs/{job_id}/score")
def score_one(job_id: str) -> dict:
    j = _require(job_id)
    return _start(f"Score: {j['company']} - {j['title']}", "score", "--job", job_id)


@app.post("/api/jobs/{job_id}/tailor")
def tailor_one(job_id: str) -> dict:
    j = _require(job_id)
    return _start(f"Tailor: {j['company']} - {j['title']}", "tailor", "--job", job_id)


@app.post("/api/enrich")
def enrich_jobs(body: LimitBody | None = None) -> dict:
    return _start("Re-run enrichment", "enrich", "--all" if body is None else "--limit",
                  str(body.limit) if body else "")


class ApplyBody(BaseModel):
    tailor: bool = False  # tailor the resume first, then auto-fill


@app.post("/api/jobs/{job_id}/apply")
def apply_one(job_id: str, body: ApplyBody | None = None) -> dict:
    j = _require(job_id)
    if not MASTER_RESUME_JSON.exists():
        raise HTTPException(400, "Upload your resume first (My resume page)")
    import json as _json
    flags = _json.loads(j["fraud_flags"] or "[]")
    if flags:
        raise HTTPException(409, "This job has trust warnings - resolve them on the job page first: "
                                 + "; ".join(flags))
    if j["status"] in ("applied", "assessment", "interview", "offer"):
        raise HTTPException(409, "Already applied to this job")
    tailor = bool(body and body.tailor)
    label = ("Tailor & apply: " if tailor else "Apply: ") + f"{j['company']} - {j['title']}"
    db.record_event(job_id, "apply", "Apply session started" + (" (with tailoring)" if tailor else ""))
    return _start(label, "apply-one", job_id, *(["--tailor"] if tailor else []))


class ApplyCommand(BaseModel):
    command: str  # fill | close


@app.post("/api/apply/command")
def apply_command(body: ApplyCommand) -> dict:
    if body.command not in ("fill", "close"):
        raise HTTPException(400, "Unknown command")
    tasks.send_apply_command(body.command)
    return {"ok": True}


@app.get("/api/task")
def task() -> dict:
    t = tasks.current()
    if not t:
        return {"task": None, "log": ""}
    return {"task": t, "log": tasks.read_log(t["log"], 20000)}


@app.post("/api/task/stop")
def stop_task() -> dict:
    tasks.stop()
    return {"ok": True}


# ---------------------------------------------------------------- analytics + notifications

@app.get("/api/analytics")
def get_analytics() -> dict:
    return analytics.dashboard()


@app.get("/api/notifications")
def get_notifications(unread: bool = False) -> list[dict]:
    return [dict(n) for n in db.notifications(unread_only=unread, limit=100)]


@app.post("/api/notifications/{nid}/read")
def read_notification(nid: int) -> dict:
    db.mark_notification_read(nid)
    return {"ok": True}


@app.post("/api/notifications/read-all")
def read_all_notifications() -> dict:
    db.mark_all_notifications_read()
    return {"ok": True}


@app.get("/api/sources/health")
def sources_health() -> dict:
    from jobagent.sources import source_health
    return source_health()


# ---------------------------------------------------------------- candidate profile + answers

@app.get("/api/profile")
def get_profile() -> dict:
    from jobagent.profile import load
    return {"profile": load().model_dump()}


@app.put("/api/profile")
def put_profile(body: dict) -> dict:
    from jobagent.models import CandidateProfile
    from jobagent.profile import load, save
    current = load()
    updates = {k: v for k, v in (body or {}).items() if k in CandidateProfile.model_fields}
    merged = current.model_copy(update=updates)
    errors = []
    try:
        merged = CandidateProfile.model_validate(merged.model_dump())
    except Exception as e:
        raise HTTPException(400, f"Invalid profile: {str(e)[:300]}")
    save(merged)
    return {"ok": True, "profile": merged.model_dump()}


class AnswerBody(BaseModel):
    question: str
    answer: str
    tags: str = ""


@app.get("/api/answers")
def list_answers() -> list[dict]:
    return [dict(a) for a in db.list_answers()]


@app.put("/api/answers")
def put_answer(body: AnswerBody) -> dict:
    if not body.question.strip() or not body.answer.strip():
        raise HTTPException(400, "Question and answer are required")
    db.upsert_answer(body.question, body.answer, body.tags)
    return {"ok": True}


@app.delete("/api/answers/{answer_id}")
def delete_answer(answer_id: int) -> dict:
    db.delete_answer(answer_id)
    return {"ok": True}


# ---------------------------------------------------------------- AI services (sync, small calls)

AI_SERVICES = ("interview_prep", "follow_up", "skill_gap", "quality_check")


@app.post("/api/ai/{service}")
def ai_service(service: str, job_id: str) -> dict:
    from jobagent import ai_services
    from jobagent.llm import LLMError
    if service not in AI_SERVICES:
        raise HTTPException(404, f"Unknown service. Available: {', '.join(AI_SERVICES)}")
    job = _require(job_id)
    try:
        return ai_services.run_service(service, job)
    except LLMError as e:
        raise HTTPException(503, str(e))


class QuestionBody(BaseModel):
    question: str
    max_chars: int = 1000


@app.post("/api/jobs/{job_id}/answer")
def answer_question(job_id: str, body: QuestionBody) -> dict:
    from jobagent import ai_services
    from jobagent.llm import LLMError
    job = _require(job_id)
    try:
        result = ai_services.screening_answer(job, body.question, body.max_chars)
    except LLMError as e:
        raise HTTPException(503, str(e))
    return result.model_dump()


@app.get("/api/ai/usage")
def ai_usage() -> dict:
    return db.ai_usage_today()


# ---------------------------------------------------------------- config + resume

def _load_yaml():
    with CONFIG_PATH.open(encoding="utf-8") as f:
        return yaml.load(f)


def _save_yaml(doc) -> None:
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        yaml.dump(doc, f)
    load_config.cache_clear()


def _plain(node):
    return json.loads(json.dumps(node, default=list))


@app.get("/api/config")
def get_config() -> dict:
    doc = _load_yaml()
    return {k: _plain(doc.get(k)) for k in ("search", "sources", "applicant", "llm")}


class ConfigBody(BaseModel):
    search: dict[str, Any] = {}
    sources: dict[str, Any] = {}
    applicant: dict[str, Any] = {}
    llm: dict[str, Any] = {}


def _merge(target, updates: dict) -> None:
    """Update the ruamel document in place so comments survive."""
    for k, v in updates.items():
        if isinstance(v, dict) and k in target and isinstance(target[k], dict):
            _merge(target[k], v)
        else:
            target[k] = v


@app.put("/api/config")
def put_config(body: ConfigBody) -> dict:
    doc = _load_yaml()
    for section in ("search", "sources", "applicant", "llm"):
        _merge(doc[section], getattr(body, section))
    _save_yaml(doc)
    from jobagent import prefit
    prefit.update(all_jobs=True)  # keywords / experience may have changed
    return {"ok": True}


@app.get("/api/resume")
def get_resume() -> dict:
    if not MASTER_RESUME_JSON.exists():
        return {"resume": None}
    return {"resume": json.loads(MASTER_RESUME_JSON.read_text(encoding="utf-8")),
            "has_preview": MASTER_RESUME_JSON.with_name("master_preview.pdf").exists(),
            "has_original": BASE_RESUME_PDF.exists()}


@app.get("/api/resume/original.pdf")
def resume_original():
    if not BASE_RESUME_PDF.exists():
        raise HTTPException(404, "No resume uploaded")
    return FileResponse(BASE_RESUME_PDF, media_type="application/pdf", content_disposition_type="inline")


@app.get("/api/resume/preview.pdf")
def resume_preview():
    p = MASTER_RESUME_JSON.with_name("master_preview.pdf")
    if not p.exists():
        raise HTTPException(404, "No preview yet")
    return FileResponse(p, media_type="application/pdf", content_disposition_type="inline")


@app.post("/api/resume/upload")
async def upload_resume(file: UploadFile = File(...)) -> dict:
    data = await file.read()
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Please upload a PDF")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(400, "Resume must be under 10 MB")
    if not data.startswith(b"%PDF"):
        raise HTTPException(400, "That file is not a valid PDF")
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    src = BASE_RESUME_PDF
    src.write_bytes(data)
    return _start("Read resume", "parse-resume", str(src))


@app.post("/api/resume/open")
def open_resume_json() -> dict:
    _open_path(MASTER_RESUME_JSON)
    return {"ok": True}


# ---------------------------------------------------------------- React app

DIST = ROOT / "web" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        # Resolve safely inside dist (no traversal outside it).
        base = (DIST / path).resolve()
        if base.is_file() and str(base).startswith(str(DIST.resolve())):
            return FileResponse(base)
        return FileResponse(DIST / "index.html")
