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
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
    for name, decl in NEW_COLUMNS.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {decl}")


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
    "verified": "verified = ?",
}


def query_jobs(filters: dict[str, Any] | None = None, search: str = "", min_score: int | None = None,
               batch_year: int | None = None, deadline_within_days: int | None = None,
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
