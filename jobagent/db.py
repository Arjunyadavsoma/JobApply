"""SQLite job tracker with idempotent migrations.

One row per job; `status` moves through the pipeline:

new -> scored | skipped -> tailored -> applied | assessment | interview | offer
     -> rejected | withdrawn | expired | dismissed     (error on failures)

Extra tables: job_events (timeline), notifications, ai_usage, answers (Q&A knowledge base).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from jobagent.config import DATA_DIR

DB_PATH = DATA_DIR / "jobs.db"

# Full pipeline statuses. `tailored` = ready to apply.
PIPELINE = ["new", "scored", "tailored", "applied", "assessment", "interview", "offer",
            "rejected", "withdrawn", "expired", "skipped", "dismissed", "error"]
ACTIVE_STATUSES = ("new", "scored", "tailored")
OUTCOME_STATUSES = ("rejected", "withdrawn", "expired", "dismissed")


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL,          -- greenhouse | lever | ashby | linkedin | naukri | ...
    company       TEXT NOT NULL,
    title         TEXT NOT NULL,
    location      TEXT,
    url           TEXT NOT NULL,          -- job posting page
    apply_url     TEXT,                   -- where the application form lives
    description   TEXT,
    posted_at     TEXT,
    status        TEXT NOT NULL DEFAULT 'new',
    score         INTEGER,
    match_json    TEXT,
    resume_path   TEXT,
    cover_path    TEXT,
    changes_json  TEXT,
    error         TEXT,
    notes         TEXT,
    discovered_at TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    applied_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_dedupe ON jobs(company, title);
"""

# Columns added after the first release. Applied idempotently by _migrate().
NEW_COLUMNS: dict[str, str] = {
    "prefit":            "INTEGER",                # quick local fit estimate (see prefit.py)
    "salary_min":        "REAL",                   # LPA (lakhs per annum, CTC)
    "salary_max":        "REAL",
    "salary_text":       "TEXT",                   # raw salary line, as advertised
    "stipend":           "INTEGER",                # monthly internship stipend, INR
    "employment_type":   "TEXT",                   # internship | full_time | part_time | contract | apprentice | trainee
    "remote_type":       "TEXT",                   # remote | hybrid | onsite | ''
    "experience_min":    "REAL",                   # years asked (0 for fresher roles)
    "experience_max":    "REAL",
    "batch_years":       "TEXT",                   # JSON list of eligible graduation years, e.g. [2026, 2027]
    "min_cgpa":          "REAL",                   # e.g. 6.5 on a 10 scale, if stated
    "min_percentage":    "REAL",                   # e.g. 60 (%), if stated
    "backlog_policy":    "TEXT",                   # 'no_active_backlogs' | 'no_backlogs' | ''
    "deadline":          "TEXT",                   # ISO date, application deadline if stated
    "fresher_flag":      "INTEGER",                # 1 = explicitly for freshers / 0-year experience
    "walk_in":           "INTEGER",                # 1 = walk-in drive
    "off_campus":        "INTEGER",                # 1 = off-campus / direct drive
    "verified":          "INTEGER DEFAULT 0",      # 1 = apply URL re-checked recently
    "last_verified_at":  "TEXT",
    "fraud_flags":       "TEXT",                   # JSON list of warnings (payment asked, ...), '' if clean
    "enriched_at":       "TEXT",                   # when deterministic enrichment last ran
    "eligibility_json":  "TEXT",                   # deterministic eligibility result (eligibility.py)
    # ---- Phase 17: verification engine + application queue ----
    "verification_state": "TEXT DEFAULT 'unverified'",  # verified_open|probably_open|needs_manual|expired|suspicious|unavailable|unverified
    "verification_note": "TEXT",                   # human-readable result summary
    "verify_method":     "TEXT",                   # http|content|sitemap|manual|...
    "final_url":         "TEXT",                   # URL after redirects
    "redirect_chain":    "TEXT",                   # JSON list of hops
    "company_domain":    "TEXT",                   # registrable domain of the apply URL
    "quality_score":     "INTEGER",                # 0-100 posting quality (separate from match score)
    "first_verified_at": "TEXT",
    "queue_state":       "TEXT DEFAULT 'new'",     # application queue state (queue.py)
    "queue_ready":       "INTEGER DEFAULT 0",      # 1 = resume tailored + answers ready
    "apply_mode":        "TEXT",                   # assisted | authorized (last execution mode)
}

EXTRA_TABLES = """
CREATE TABLE IF NOT EXISTS job_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    kind        TEXT NOT NULL,      -- status | note | error | verify | apply | enrich | ai
    text        TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_job ON job_events(job_id, created_at);

CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,      -- deadline | new_match | error | status | system
    title       TEXT NOT NULL,
    body        TEXT,
    job_id      TEXT,
    read        INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ai_usage (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    task              TEXT NOT NULL,        -- score | tailor | form | parse | interview_prep | ...
    backend           TEXT,                 -- groq | api | subscription
    model             TEXT,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    cached            INTEGER NOT NULL DEFAULT 0,
    duration_ms       INTEGER,
    ok                INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS answers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    question    TEXT NOT NULL,
    answer      TEXT NOT NULL,
    tags        TEXT,                        -- comma separated, e.g. 'why_this_company,reusable'
    updated_at  TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_answers_question ON answers(question);

CREATE TABLE IF NOT EXISTS apply_sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    mode        TEXT NOT NULL,               -- assisted | authorized
    state_json  TEXT NOT NULL,               -- progress: step, filled fields, files, screenshots
    status      TEXT NOT NULL DEFAULT 'open',-- open | completed | aborted | needs_human
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_job ON apply_sessions(job_id, updated_at);

CREATE TABLE IF NOT EXISTS email_messages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id    TEXT NOT NULL,             -- RFC822 Message-ID (dedup key)
    sender        TEXT,
    subject       TEXT,
    snippet       TEXT,
    received_at   TEXT NOT NULL,
    classification TEXT,                     -- confirmation|assessment|interview|rejection|offer|recruiter|other
    confidence    REAL,                      -- 0-1
    matched_job_id TEXT,
    reviewed      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_email_msgid ON email_messages(message_id);
CREATE INDEX IF NOT EXISTS idx_email_job ON email_messages(matched_job_id);

CREATE TABLE IF NOT EXISTS task_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    label       TEXT NOT NULL,
    args        TEXT,                        -- JSON list
    pid         INTEGER,
    log         TEXT,
    started_at  TEXT NOT NULL,
    ended_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_task_history_time ON task_history(started_at);

CREATE TABLE IF NOT EXISTS submissions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    mode        TEXT NOT NULL,               -- assisted | authorized | manual
    evidence    TEXT,                        -- JSON: confirmation text, screenshot paths, final URL
    confirmed   INTEGER NOT NULL DEFAULT 0,  -- 1 = platform confirmed the submission
    submitted_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_submissions_job ON submissions(job_id);

-- ---- Phase 18: application execution platform ----

CREATE TABLE IF NOT EXISTS company_intel (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    company     TEXT NOT NULL,               -- lower-cased company name (dedup key)
    intel_json  TEXT NOT NULL,               -- CompanyIntel model: verified facts + AI interpretation
    sources     TEXT,                        -- JSON list of source URLs actually fetched
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_company_intel ON company_intel(company);

CREATE TABLE IF NOT EXISTS agent_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent       TEXT NOT NULL,               -- agent key, e.g. company_research
    job_id      TEXT,
    prompt_version TEXT NOT NULL,
    model       TEXT,
    duration_ms INTEGER,
    cached      INTEGER NOT NULL DEFAULT 0,
    ok          INTEGER NOT NULL DEFAULT 1,
    error       TEXT,
    result_summary TEXT,                     -- first ~300 chars of the structured result
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_agent ON agent_runs(agent, created_at);
CREATE INDEX IF NOT EXISTS idx_agent_runs_job ON agent_runs(job_id);

CREATE TABLE IF NOT EXISTS interview_prep (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    plan_json   TEXT NOT NULL,               -- InterviewPrepPlan model
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_interview_prep_job ON interview_prep(job_id);

CREATE TABLE IF NOT EXISTS prep_progress (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    category    TEXT NOT NULL,               -- company|technical|python|sql|dsa|ai_ml|project|hr|behavioral
    question_id TEXT NOT NULL,
    attempted   INTEGER NOT NULL DEFAULT 0,  -- times shown in a mock session
    answered    INTEGER NOT NULL DEFAULT 0,  -- times the candidate answered (self-assessed)
    last_at     TEXT NOT NULL,
    UNIQUE(job_id, category, question_id)
);

CREATE TABLE IF NOT EXISTS apply_locks (
    job_id      TEXT PRIMARY KEY,            -- one browser session per application
    owner       TEXT NOT NULL,               -- task label / pid that holds the lock
    acquired_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS batch_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    label       TEXT NOT NULL,
    job_ids     TEXT NOT NULL,               -- JSON list
    controls    TEXT,                        -- JSON: the gates this batch ran under
    result      TEXT,                        -- JSON: per-job outcome summary
    created_at  TEXT NOT NULL,
    finished_at TEXT
);
"""

# Performance indexes for the hot paths (queue listing, dashboard, email review).
PERF_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_jobs_queue ON jobs(queue_state, COALESCE(score, prefit, 0) DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(lower(company));
CREATE INDEX IF NOT EXISTS idx_submissions_day ON submissions(submitted_at);
CREATE INDEX IF NOT EXISTS idx_emails_received ON email_messages(received_at);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def job_id(source: str, external_id: str) -> str:
    return hashlib.sha1(f"{source}:{external_id}".encode()).hexdigest()[:16]


@dataclass
class Job:
    source: str
    external_id: str
    company: str
    title: str
    url: str
    location: str | None = None
    apply_url: str | None = None
    description: str | None = None
    posted_at: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def id(self) -> str:
        return job_id(self.source, self.external_id)


# ---------------------------------------------------------------- connection + migrations

def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent: creates missing tables and adds missing columns. Never drops data."""
    conn.executescript(SCHEMA)
    conn.executescript(EXTRA_TABLES)
    for stmt in PERF_INDEXES.strip().split(";"):
        if stmt.strip():
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError:      # older sqlite without expression indexes
                continue
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
    for name, decl in NEW_COLUMNS.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {decl}")
    # Phase 18: entity storage on email_messages (older installs lack the column)
    email_cols = {r["name"] for r in conn.execute("PRAGMA table_info(email_messages)")}
    if "entities" not in email_cols:
        conn.execute("ALTER TABLE email_messages ADD COLUMN entities TEXT")


# ---------------------------------------------------------------- verification + queue helpers

VERIFICATION_STATES = ("verified_open", "probably_open", "needs_manual", "expired",
                       "suspicious", "unavailable", "unverified")

QUEUE_STATES = ("new", "analyzing", "eligible", "shortlisted", "resume_prep", "ready",
                "awaiting_approval", "applying", "submitted", "failed", "needs_manual")


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _migrate(conn)
    return conn


# ---------------------------------------------------------------- jobs

def _norm_title(t: str) -> str:
    import re
    return re.sub(r"\s+", " ", (t or "").strip().lower())


def upsert_jobs(jobs: Iterable[Job]) -> int:
    """Insert new jobs; skip ones already stored or duplicated across sources. Returns # inserted."""
    inserted = 0
    with connect() as conn:
        for j in jobs:
            if conn.execute("SELECT 1 FROM jobs WHERE id = ?", (j.id,)).fetchone():
                continue
            # Same company + (normalised) title seen from another source (e.g. a LinkedIn
            # mirror of a Greenhouse post) counts as a duplicate.
            dup = conn.execute(
                "SELECT 1 FROM jobs WHERE lower(company) = lower(?) AND lower(title) = lower(?)",
                (j.company, j.title),
            ).fetchone()
            if dup:
                continue
            ts = now()
            cur = conn.execute(
                """INSERT INTO jobs (id, source, company, title, location, url, apply_url,
                   description, posted_at, discovered_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (j.id, j.source, j.company, j.title, j.location, j.url, j.apply_url or j.url,
                 j.description, j.posted_at, ts, ts),
            )
            record_event(conn, j.id, "discovered", f"Found on {j.source}")
            inserted += 1
    return inserted


def update(job_id_: str, **fields: Any) -> None:
    clean = {}
    for k, v in fields.items():
        clean[k] = json.dumps(v) if isinstance(v, (dict, list)) else v
    clean["updated_at"] = now()
    cols = ", ".join(f"{k} = ?" for k in clean)
    with connect() as conn:
        conn.execute(f"UPDATE jobs SET {cols} WHERE id = ?", (*clean.values(), job_id_))


def get(job_id_: str) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id_,)).fetchone()


def by_status(*statuses: str,
              order: str = "score DESC, prefit DESC, discovered_at DESC") -> list[sqlite3.Row]:
    q = ",".join("?" * len(statuses))
    with connect() as conn:
        return conn.execute(
            f"SELECT * FROM jobs WHERE status IN ({q}) ORDER BY {order}", statuses
        ).fetchall()


def all_jobs() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute("SELECT * FROM jobs ORDER BY discovered_at DESC").fetchall()


ALLOWED_FILTERS = {
    "status": "status = ?", "source": "source = ?", "company": "lower(company) LIKE lower(?)",
    "employment_type": "employment_type = ?", "remote_type": "remote_type = ?",
    "fresher_flag": "fresher_flag = ?", "walk_in": "walk_in = ?", "off_campus": "off_campus = ?",
    "verified": "verified = ?", "verification_state": "verification_state = ?",
    "queue_state": "queue_state = ?",
}


def query_jobs(filters: dict[str, Any] | None = None, search: str = "", min_score: int | None = None,
               batch_year: int | None = None, deadline_within_days: int | None = None,
               location: str = "", min_salary: float | None = None, max_experience: float | None = None,
               skills: str = "",
               order: str = "score DESC, prefit DESC, discovered_at DESC",
               limit: int = 200, offset: int = 0) -> list[sqlite3.Row]:
    """Filtered + paginated job query used by the API. Values are matched exactly per
    ALLOWED_FILTERS except company (LIKE). Unknown filters are ignored."""
    where, params = [], []
    for key, value in (filters or {}).items():
        rule = ALLOWED_FILTERS.get(key)
        if rule and value not in (None, ""):
            where.append(rule)
            params.append(value)
    if search:
        where.append("(lower(title) LIKE lower(?) OR lower(company) LIKE lower(?))")
        params += [f"%{search}%"] * 2
    if location:
        where.append("(lower(COALESCE(location,'')) LIKE lower(?) OR remote_type = 'remote')")
        params.append(f"%{location}%")
    if skills:
        where.append("lower(COALESCE(description,'')) LIKE lower(?)")
        params.append(f"%{skills}%")
    if min_salary is not None:
        # salary floors are CTC in LPA on the jobs table
        where.append("(COALESCE(salary_max, salary_min, 0) >= ? OR stipend > 0)")
        params.append(min_salary)
    if max_experience is not None:
        where.append("(COALESCE(experience_min, 99) <= ?)")
        params.append(max_experience)
    if min_score is not None:
        where.append("COALESCE(score, prefit, 0) >= ?")
        params.append(min_score)
    if batch_year is not None:
        # batch_years is a JSON list like "[2026, 2027]"; substring match is sufficient here.
        where.append("batch_years LIKE ?")
        params.append(f"%{batch_year}%")
    if deadline_within_days is not None:
        where.append("deadline IS NOT NULL AND deadline != '' AND date(deadline) BETWEEN"
                     " date('now') AND date('now', ?)")
        params.append(f"+{int(deadline_within_days)} days")
    sql = f"SELECT * FROM jobs {'WHERE ' + ' AND '.join(where) if where else ''} ORDER BY {order} LIMIT ? OFFSET ?"
    with connect() as conn:
        return conn.execute(sql, (*params, limit, offset)).fetchall()


def stats() -> dict[str, int]:
    with connect() as conn:
        rows = conn.execute("SELECT status, COUNT(*) c FROM jobs GROUP BY status").fetchall()
        counts = {r["status"]: r["c"] for r in rows}
    out = {s: counts.get(s, 0) for s in PIPELINE}
    out["total"] = sum(counts.values())
    return out


# ---------------------------------------------------------------- job events (timeline)

def record_event(conn_or_id, job_id: str | None = None, kind: str = "note", text: str = "") -> None:
    """record_event(conn, job_id, kind, text) inside a transaction, or
    record_event(job_id, kind, text) standalone."""
    if isinstance(conn_or_id, sqlite3.Connection):
        conn = conn_or_id
        jid = job_id
    else:
        jid, kind, text = conn_or_id, job_id or "note", kind
        conn = connect()
        try:
            with conn:
                conn.execute("INSERT INTO job_events (job_id, kind, text, created_at) VALUES (?,?,?,?)",
                             (jid, kind, text, now()))
            return
        finally:
            conn.close()
    conn.execute("INSERT INTO job_events (job_id, kind, text, created_at) VALUES (?,?,?,?)",
                 (jid, kind, text, now()))


def events_for(job_id_: str, limit: int = 100) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM job_events WHERE job_id = ? ORDER BY id DESC LIMIT ?", (job_id_, limit)
        ).fetchall()


def set_status(job_id_: str, status: str, note: str = "") -> bool:
    """Move a job through the pipeline; records a timeline event. Returns False on bad status."""
    if status not in PIPELINE:
        return False
    extra = {}
    if status == "applied":
        extra["applied_at"] = now()
    if status == "expired":
        extra["error"] = None
    update(job_id_, status=status, error=None, **extra)
    record_event(job_id_, "status", f"Status -> {status}" + (f" ({note})" if note else ""))
    return True


# ---------------------------------------------------------------- notifications

def add_notification(kind: str, title: str, body: str = "", job_id: str | None = None) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO notifications (kind, title, body, job_id, read, created_at) VALUES (?,?,?,?,0,?)",
            (kind, title, body, job_id, now()),
        )
        return cur.lastrowid


def notifications(unread_only: bool = False, limit: int = 100) -> list[sqlite3.Row]:
    where = "WHERE read = 0" if unread_only else ""
    with connect() as conn:
        return conn.execute(
            f"SELECT * FROM notifications {where} ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()


def mark_notification_read(nid: int, read: bool = True) -> None:
    with connect() as conn:
        conn.execute("UPDATE notifications SET read = ? WHERE id = ?", (int(read), nid))


def mark_all_notifications_read() -> None:
    with connect() as conn:
        conn.execute("UPDATE notifications SET read = 1")


# ---------------------------------------------------------------- AI usage

def track_ai_usage(task: str, backend: str, model: str, prompt_tokens: int = 0,
                   completion_tokens: int = 0, cached: bool = False, duration_ms: int = 0,
                   ok: bool = True) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO ai_usage (task, backend, model, prompt_tokens, completion_tokens, cached,"
            " duration_ms, ok, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (task, backend, model, prompt_tokens, completion_tokens, int(cached),
             duration_ms, int(ok), now()),
        )


def ai_usage_today() -> dict[str, int]:
    day = now()[:10]
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) calls, SUM(cached) cached,"
            " SUM(COALESCE(prompt_tokens,0)+COALESCE(completion_tokens,0)) tokens"
            " FROM ai_usage WHERE created_at LIKE ?", (f"{day}%",),
        ).fetchone()
    return {"calls": row["calls"] or 0, "cached": row["cached"] or 0, "tokens": row["tokens"] or 0}


# ---------------------------------------------------------------- answer knowledge base

def list_answers() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute("SELECT * FROM answers ORDER BY updated_at DESC").fetchall()


def upsert_answer(question: str, answer: str, tags: str = "") -> None:
    with connect() as conn:
        conn.execute(
            """INSERT INTO answers (question, answer, tags, updated_at) VALUES (?,?,?,?)
               ON CONFLICT(question) DO UPDATE SET answer = excluded.answer,
                   tags = excluded.tags, updated_at = excluded.updated_at""",
            (question.strip(), answer.strip(), tags.strip(), now()),
        )


def delete_answer(answer_id: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM answers WHERE id = ?", (answer_id,))


def find_answer(question: str) -> sqlite3.Row | None:
    """Exact or keyword-ish lookup in the verified answer knowledge base."""
    q = (question or "").strip().lower()
    if not q:
        return None
    with connect() as conn:
        exact = conn.execute("SELECT * FROM answers WHERE lower(question) = ?", (q,)).fetchone()
        if exact:
            return exact
        rows = conn.execute("SELECT * FROM answers").fetchall()
    words = {w for w in q.replace("?", " ").split() if len(w) > 3}
    best, best_score = None, 0
    for r in rows:
        target = f"{r['question']} {r['tags']}".lower()
        score = sum(1 for w in words if w in target)
        if score > best_score:
            best, best_score = r, score
    return best if best_score >= 2 else None


# ---------------------------------------------------------------- apply sessions (17.5/17.6)

def save_session(job_id: str, mode: str, state: dict) -> None:
    """Persist apply progress so an interrupted session can be resumed."""
    ts = now()
    with connect() as conn:
        row = conn.execute("SELECT id FROM apply_sessions WHERE job_id = ? AND status = 'open'",
                           (job_id,)).fetchone()
        if row:
            conn.execute("UPDATE apply_sessions SET state_json = ?, updated_at = ? WHERE id = ?",
                         (json.dumps(state), ts, row["id"]))
        else:
            conn.execute("INSERT INTO apply_sessions (job_id, mode, state_json, status, created_at,"
                         " updated_at) VALUES (?,?,?,?,?,?)",
                         (job_id, mode, json.dumps(state), "open", ts, ts))


def load_session(job_id: str) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute("SELECT * FROM apply_sessions WHERE job_id = ? AND status = 'open' "
                            "ORDER BY id DESC", (job_id,)).fetchone()


def close_session(job_id: str, status: str = "completed") -> None:
    with connect() as conn:
        conn.execute("UPDATE apply_sessions SET status = ?, updated_at = ? WHERE job_id = ? "
                     "AND status = 'open'", (status, now(), job_id))


# ---------------------------------------------------------------- submissions (17.6 evidence)

def record_submission(job_id: str, mode: str, evidence: dict, confirmed: bool) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO submissions (job_id, mode, evidence, confirmed, submitted_at) "
                     "VALUES (?,?,?,?,?)",
                     (job_id, mode, json.dumps(evidence), int(confirmed), now()))


def submissions_for(job_id: str) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute("SELECT * FROM submissions WHERE job_id = ? ORDER BY id DESC",
                            (job_id,)).fetchall()


# ---------------------------------------------------------------- Phase 18 tables

def save_company_intel(company: str, intel: dict, sources: list[str]) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO company_intel (company, intel_json, sources, created_at, updated_at) "
            "VALUES (?,?,?,?,?) ON CONFLICT(company) DO UPDATE SET "
            "intel_json=excluded.intel_json, sources=excluded.sources, "
            "updated_at=excluded.updated_at",
            (company.strip().lower(), json.dumps(intel), json.dumps(sources), now(), now()))


def get_company_intel(company: str, max_age_hours: int = 24 * 7) -> dict | None:
    """Cached company research, or None when absent/stale."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM company_intel WHERE company = ?",
                           (company.strip().lower(),)).fetchone()
    if not row:
        return None
    try:
        age = (datetime.now(timezone.utc)
               - datetime.fromisoformat(row["updated_at"])).total_seconds() / 3600
    except ValueError:
        age = max_age_hours + 1
    if age > max_age_hours:
        return None
    try:
        return {"intel": json.loads(row["intel_json"]),
                "sources": json.loads(row["sources"] or "[]"),
                "updated_at": row["updated_at"]}
    except ValueError:
        return None


def record_agent_run(agent: str, job_id: str | None, prompt_version: str, model: str = "",
                     duration_ms: int = 0, cached: bool = False, ok: bool = True,
                     error: str = "", result_summary: str = "") -> None:
    """One row per agent execution — the audit trail behind /api/agents/history."""
    with connect() as conn:
        conn.execute(
            "INSERT INTO agent_runs (agent, job_id, prompt_version, model, duration_ms, "
            "cached, ok, error, result_summary, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (agent, job_id, prompt_version, model, int(duration_ms), int(cached), int(ok),
             error[:500], result_summary[:300], now()))


def agent_runs(limit: int = 100, agent: str = "", job_id: str = "") -> list[sqlite3.Row]:
    q, args = "SELECT * FROM agent_runs", []
    where = []
    if agent:
        where.append("agent = ?")
        args.append(agent)
    if job_id:
        where.append("job_id = ?")
        args.append(job_id)
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    with connect() as conn:
        return conn.execute(q, args).fetchall()


def save_interview_prep(job_id: str, plan: dict) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO interview_prep (job_id, plan_json, created_at, updated_at) "
            "VALUES (?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET "
            "plan_json=excluded.plan_json, updated_at=excluded.updated_at",
            (job_id, json.dumps(plan), now(), now()))


def get_interview_prep(job_id: str) -> dict | None:
    with connect() as conn:
        row = conn.execute("SELECT plan_json FROM interview_prep WHERE job_id = ?",
                           (job_id,)).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["plan_json"])
    except ValueError:
        return None


def track_prep_progress(job_id: str, category: str, question_id: str,
                        answered: bool) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO prep_progress (job_id, category, question_id, attempted, answered, "
            "last_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(job_id, category, question_id) DO UPDATE SET "
            "attempted = prep_progress.attempted + 1, "
            "answered = prep_progress.answered + ?, last_at = excluded.last_at",
            (job_id, category, question_id, 1, int(answered), now(), int(answered)))


def prep_progress_summary(job_id: str) -> dict:
    """Per-category progress for the dashboard: attempted/answered counts."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT category, COUNT(*) questions, SUM(attempted) attempted, "
            "SUM(answered) answered FROM prep_progress WHERE job_id = ? GROUP BY category",
            (job_id,)).fetchall()
    return {r["category"]: {"questions": r["questions"], "attempted": r["attempted"] or 0,
                            "answered": r["answered"] or 0} for r in rows}


def acquire_apply_lock(job_id: str, owner: str, max_age_seconds: int = 3600) -> bool:
    """One browser session per application. A stale lock (>max_age) is taken over."""
    with connect() as conn:
        row = conn.execute("SELECT * FROM apply_locks WHERE job_id = ?", (job_id,)).fetchone()
        if row:
            try:
                age = (datetime.now(timezone.utc)
                       - datetime.fromisoformat(row["acquired_at"])).total_seconds()
            except ValueError:
                age = max_age_seconds + 1
            if age < max_age_seconds:
                return False                       # held by a live session
            conn.execute("DELETE FROM apply_locks WHERE job_id = ?", (job_id,))
        conn.execute("INSERT OR REPLACE INTO apply_locks (job_id, owner, acquired_at) "
                     "VALUES (?,?,?)", (job_id, owner, now()))
        return True


def release_apply_lock(job_id: str, owner: str | None = None) -> None:
    with connect() as conn:
        if owner:
            conn.execute("DELETE FROM apply_locks WHERE job_id = ? AND owner = ?",
                         (job_id, owner))
        else:
            conn.execute("DELETE FROM apply_locks WHERE job_id = ?", (job_id,))


def apply_lock_holder(job_id: str) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT owner FROM apply_locks WHERE job_id = ?",
                           (job_id,)).fetchone()
    return row["owner"] if row else None


def create_batch_run(label: str, job_ids: list[str], controls: dict) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO batch_runs (label, job_ids, controls, created_at) VALUES (?,?,?,?)",
            (label, json.dumps(job_ids), json.dumps(controls), now()))
        return int(cur.lastrowid)


def finish_batch_run(batch_id: int, result: dict) -> None:
    with connect() as conn:
        conn.execute("UPDATE batch_runs SET result = ?, finished_at = ? WHERE id = ?",
                     (json.dumps(result), now(), batch_id))


def batch_runs(limit: int = 20) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute("SELECT * FROM batch_runs ORDER BY id DESC LIMIT ?",
                            (limit,)).fetchall()


# ---------------------------------------------------------------- email messages (17.9)

def upsert_email(msg: dict) -> bool:
    """Insert one classified email; returns False if the Message-ID was already stored."""
    entities = msg.get("entities")
    entities_json = json.dumps(entities) if entities else None
    with connect() as conn:
        if conn.execute("SELECT 1 FROM email_messages WHERE message_id = ?",
                        (msg["message_id"],)).fetchone():
            return False
        conn.execute("INSERT INTO email_messages (message_id, sender, subject, snippet, received_at,"
                     " classification, confidence, matched_job_id, reviewed, entities, created_at)"
                     " VALUES (?,?,?,?,?,?,?,?,0,?,?)",
                     (msg["message_id"], msg.get("sender", ""), msg.get("subject", ""),
                      msg.get("snippet", ""), msg.get("received_at", now()),
                      msg.get("classification"), msg.get("confidence"),
                      msg.get("matched_job_id"), entities_json, now()))
        return True


def emails(unreviewed_only: bool = False, limit: int = 200) -> list[sqlite3.Row]:
    where = "WHERE reviewed = 0" if unreviewed_only else ""
    with connect() as conn:
        return conn.execute(f"SELECT * FROM email_messages {where} ORDER BY received_at DESC "
                            f"LIMIT ?", (limit,)).fetchall()


def review_email(email_id: int, classification: str | None = None,
                 matched_job_id: str | None = None) -> None:
    with connect() as conn:
        if classification is not None:
            conn.execute("UPDATE email_messages SET classification = ?, confidence = 1.0, "
                         "reviewed = 1 WHERE id = ?", (classification, email_id))
        elif matched_job_id is not None:
            conn.execute("UPDATE email_messages SET matched_job_id = ?, reviewed = 1 WHERE id = ?",
                         (matched_job_id, email_id))
        else:
            conn.execute("UPDATE email_messages SET reviewed = 1 WHERE id = ?", (email_id,))


# ---------------------------------------------------------------- task history (17.11)

def record_task_start(label: str, args: list, pid: int, log: str) -> int:
    with connect() as conn:
        cur = conn.execute("INSERT INTO task_history (label, args, pid, log, started_at) "
                           "VALUES (?,?,?,?,?)",
                           (label, json.dumps(args), pid, log, now()))
        return cur.lastrowid


def record_task_end(task_row_id: int) -> None:
    with connect() as conn:
        conn.execute("UPDATE task_history SET ended_at = ? WHERE id = ?", (now(), task_row_id))


def task_history(limit: int = 50) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute("SELECT * FROM task_history ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
