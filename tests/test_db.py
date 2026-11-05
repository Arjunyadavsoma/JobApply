"""Database layer: migrations, dedup, events, notifications, answers, filtered queries."""
from __future__ import annotations

import json

from jobagent import db
from jobagent.db import Job


def _job(n: int, **kw) -> Job:
    base = dict(source="test", external_id=str(n), company=f"Corp{n}", title="Software Engineer",
                url=f"https://x/{n}", description="Python ML role for 2027 batch")
    base.update(kw)
    return Job(**base)


def test_migrations_idempotent(env):
    with db.connect() as c1, db.connect() as c2:
        cols = {r["name"] for r in c2.execute("PRAGMA table_info(jobs)")}
    for col in ("salary_min", "batch_years", "fraud_flags", "deadline", "eligibility_json"):
        assert col in cols
    tables = {r["name"] for r in c2.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"job_events", "notifications", "ai_usage", "answers"} <= tables


def test_insert_and_dedup_same_id(env):
    assert db.upsert_jobs([_job(1)]) == 1
    assert db.upsert_jobs([_job(1)]) == 0                       # same id
    # same company + title from another source is a duplicate too
    assert db.upsert_jobs([_job(2, source="other", company="Corp1")]) == 0


def test_dedup_case_insensitive_title(env):
    assert db.upsert_jobs([_job(1, title="Data Scientist")]) == 1
    assert db.upsert_jobs([_job(2, company="Corp1", title="data scientist")]) == 0


def test_status_pipeline_and_events(env):
    j = _job(1)
    db.upsert_jobs([j])
    assert db.set_status(j.id, "scored", "test")
    assert db.set_status(j.id, "applied")
    row = db.get(j.id)
    assert row["status"] == "applied" and row["applied_at"]
    events = db.events_for(j.id)
    kinds = [e["kind"] for e in events]
    assert "discovered" in kinds and "status" in kinds
    assert db.set_status(j.id, "bogus") is False                 # unknown status rejected


def test_duplicate_applied_guard_via_status(env):
    j = _job(1)
    db.upsert_jobs([j])
    db.set_status(j.id, "applied")
    # the API-level guard is tested in test_api; here set_status stays permissive by design
    db.set_status(j.id, "interview")
    assert db.get(j.id)["status"] == "interview"


def test_notifications(env):
    db.add_notification("deadline", "Deadline soon", "Apply by tomorrow", "job1")
    db.add_notification("status", "Interview!", "Corp A", "job2")
    assert len(db.notifications()) == 2
    assert len(db.notifications(unread_only=True)) == 2
    db.mark_notification_read(1)
    assert len(db.notifications(unread_only=True)) == 1
    db.mark_all_notifications_read()
    assert len(db.notifications(unread_only=True)) == 0


def test_ai_usage_tracking(env):
    db.track_ai_usage("score", "groq", "gpt-oss-120b", 100, 200, duration_ms=900)
    db.track_ai_usage("score", "groq", "gpt-oss-120b", cached=True)
    today = db.ai_usage_today()
    assert today["calls"] == 2 and today["cached"] == 1 and today["tokens"] == 300


def test_answers_knowledge_base(env):
    db.upsert_answer("Why do you want to work here?", "Your AI platform matches my ML projects.")
    db.upsert_answer("Why do you want to work here?", "Updated answer")     # upsert, not duplicate
    rows = db.list_answers()
    assert len(rows) == 1 and rows[0]["answer"] == "Updated answer"
    hit = db.find_answer("why do you want to work here")
    assert hit is not None
    assert db.find_answer("notice period?") is None                           # nothing close enough
    db.delete_answer(rows[0]["id"])
    assert db.list_answers() == []


def test_query_filters(env):
    db.upsert_jobs([
        _job(1, title="ML Engineer", company="Alpha"),
        _job(2, title="Java Architect", company="Beta"),
    ])
    db.update(db.job_id("test", "1"), fresher_flag=1, batch_years=[2027], score=80, status="scored")
    assert len(db.query_jobs()) == 2
    assert len(db.query_jobs({"fresher_flag": 1})) == 1
    assert len(db.query_jobs(batch_year=2027)) == 1
    assert len(db.query_jobs(search="alpha")) == 1
    assert len(db.query_jobs(min_score=75)) == 1
    assert len(db.query_jobs({"status": "scored"})) == 1


def test_stats_counts_all_statuses(env):
    db.upsert_jobs([_job(1)])
    db.set_status(db.job_id("test", "1"), "applied")
    s = db.stats()
    assert s["applied"] == 1 and s["total"] == 1 and s["new"] == 0
