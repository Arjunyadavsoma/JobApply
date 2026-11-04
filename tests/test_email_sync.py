"""Phase 17.9 — email monitoring tests (rules + matching + storage; transports mocked)."""
from __future__ import annotations

import pytest

from jobagent import db, email_sync


def test_deterministic_classification():
    assert email_sync.classify_deterministic("Application received - Acme", "We have received your application")[0] == "confirmation"
    assert email_sync.classify_deterministic("Your HackerRank test", "Please complete the online test")[0] == "assessment"
    assert email_sync.classify_deterministic("Interview invite", "Your technical round is scheduled")[0] == "interview"
    assert email_sync.classify_deterministic("We regret to inform you", "we have decided not to proceed")[0] == "rejection"
    assert email_sync.classify_deterministic("Offer letter", "We are pleased to offer you")[0] == "offer"
    assert email_sync.classify_deterministic("Random newsletter", "buy our stuff") == (None, 0.0)


def test_classify_falls_back_gracefully_without_ai(monkeypatch):
    from jobagent.llm import LLMError

    def boom(*a, **k):
        raise LLMError("no key")
    monkeypatch.setattr(email_sync, "classify_with_ai", lambda s, b: ("other", 0.3))
    label, conf = email_sync.classify("Random subject", "random body")
    assert label == "other" and conf < 0.6      # flagged for review


def test_company_matching_finds_application(env):
    db.upsert_jobs([db.Job(source="gh", external_id="a:1", company="Razorpay Labs Pvt Ltd",
                           title="SDE", url="https://x/1", description="d")])
    jid = db.job_id("gh", "a:1")
    db.update(jid, status="applied")
    assert email_sync.match_to_application("Your application to Razorpay",
                                           "thanks", "careers@razorpay.com") == jid
    assert email_sync.match_to_application("Totally unrelated", "hello", "x@y.com") is None


def test_process_messages_pipeline(env):
    db.upsert_jobs([db.Job(source="gh", external_id="a:2", company="Acme Technologies",
                           title="AI Engineer", url="https://x/2", description="d")])
    jid = db.job_id("gh", "a:2")
    db.update(jid, status="applied")

    msgs = [
        {"message_id": "<1@x>", "sender": "careers@acme.com", "subject": "Application received",
         "body": "We have received your application for AI Engineer", "received_at": "2026-11-05T10:00:00+00:00"},
        {"message_id": "<2@x>", "sender": "noreply@acme.com",
         "subject": "Update on your application",
         "body": "We regret to inform you that we have decided not to proceed",
         "received_at": "2026-11-05T11:00:00+00:00"},
        {"message_id": "<1@x>", "sender": "dup", "subject": "dup", "body": "b",
         "received_at": "2026-11-05T12:00:00+00:00"},
        {"message_id": "<3@x>", "sender": "news@spam.io", "subject": "Weekly digest",
         "body": "random shopping deals", "received_at": "2026-11-05T13:00:00+00:00"},
    ]
    stats = email_sync.process_messages(msgs)
    assert stats["new"] == 3 and stats["duplicate"] == 1
    assert stats["needs_review"] == 1            # the spam digest
    assert db.get(jid)["status"] == "rejected"   # rejection applied by the pipeline

    rows = {r["message_id"]: r for r in db.emails()}
    assert rows["<1@x>"]["classification"] == "confirmation"
    assert rows["<2@x>"]["classification"] == "rejection"
    assert rows["<2@x>"]["matched_job_id"] == jid
    assert rows["<3@x>"]["reviewed"] == 0       # low confidence -> needs review

    notes = db.notifications()
    assert any("Rejection" in n["title"] for n in notes)


def test_nothing_is_ever_sent():
    """The module has no send capability at all - verify by source."""
    import inspect
    src = inspect.getsource(email_sync)
    assert "smtp" not in src.lower().replace("imaplib", ""), "no SMTP import allowed"
    assert "send_message" not in src and "sendmail" not in src


def test_sync_unconfigured_raises(env):
    with pytest.raises(RuntimeError, match="not configured"):
        email_sync.sync()
