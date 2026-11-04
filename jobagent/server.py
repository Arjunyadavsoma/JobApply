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
               "verified", "fraud_flags", "notes", "min_cgpa", "min_percentage")


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

@app.get("/api/health")
def health() -> dict:
    """Liveness/readiness for deployment checks. No auth, no data."""
    return {"ok": True, "version": "2.0", "pipeline": db.stats().get("total", 0) > 0}


@app.get("/api/stats")
def stats() -> dict:
    s = db.stats()
    s["has_resume"] = MASTER_RESUME_JSON.exists()
    s["has_profile"] = (PROFILE_DIR / "candidate_profile.json").exists()
    return s


# ---------------------------------------------------------------- jobs (filtered list + India views)

@app.get("/api/jobs")
def list_jobs(status: str = "", source: str = "", employment_type: str = "", remote_type: str = "",
              fresher: int = 0, walk_in: int = 0, off_campus: int = 0, batch_year: int = 0,
              search: str = "", min_score: int = 0, deadline_days: int = 0, limit: int = 300,
              offset: int = 0) -> list[dict]:
    filters = {k: v for k, v in {"status": status, "source": source,
                                 "employment_type": employment_type,
                                 "remote_type": remote_type, "fresher_flag": fresher or None,
                                 "walk_in": walk_in or None, "off_campus": off_campus or None}.items() if v}
    rows = db.query_jobs(filters, search=search or "", min_score=min_score or None,
                         batch_year=batch_year or None,
                         deadline_within_days=deadline_days or None,
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
    """Re-check the apply URL (HEAD, then GET fallback) and refresh the verification stamp."""
    import httpx
    r = _require(job_id)
    url = r["apply_url"] or r["url"]
    ok, status = False, ""
    try:
        with httpx.Client(follow_redirects=True, timeout=20) as http:
            resp = http.head(url)
            if resp.status_code >= 400:      # some sites reject HEAD
                resp = http.get(url)
            ok = resp.status_code < 400
            status = f"HTTP {resp.status_code}"
    except httpx.HTTPError as e:
        status = str(e).splitlines()[0][:120]
    db.update(job_id, verified=1 if ok else 0, last_verified_at=db.now() if ok else None)
    db.record_event(job_id, "verify", f"Apply URL check: {status}")
    return {"ok": ok, "status": status}


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
