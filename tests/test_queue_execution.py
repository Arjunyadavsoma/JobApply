"""Phase 17.6/17.7 — application queue + execution modes tests (offline)."""
from __future__ import annotations

import json

import pytest

from jobagent import db, execution, queue


def _mk_job(env, n="1", **kw):
    db.upsert_jobs([db.Job(source="greenhouse", external_id=f"acme:{n}",
                           company=kw.pop("company", "Acme"),
                           title=kw.pop("title", f"AI Engineer {n}"),
                           url=f"https://boards.greenhouse.io/acme/jobs/{n}",
                           apply_url=f"https://boards.greenhouse.io/acme/jobs/{n}",
                           description="python ml role. Apply now.")])
    jid = db.job_id("greenhouse", f"acme:{n}")
    if kw:
        db.update(jid, **kw)
    return jid


def test_queue_states_flow_and_event_log(env):
    jid = _mk_job(env)
    assert queue.set_queue_state(jid, "shortlisted")
    assert not queue.set_queue_state(jid, "bogus-state")
    ev = db.events_for(jid)
    assert any(e["kind"] == "queue" and "shortlisted" in (e["text"] or "") for e in ev)


def test_can_apply_gates(env):
    jid = _mk_job(env, score=85, verification_state="verified_open", status="scored",
                  eligibility_json=json.dumps({"status": "eligible"}))
    ok, reason = queue.can_apply(jid)
    assert ok, reason

    # fraud flags block
    jid2 = _mk_job(env, "2", fraud_flags='["asks for payment"]', score=85,
                   verification_state="verified_open", eligibility_json='{"status": "eligible"}')
    ok, reason = queue.can_apply(jid2)
    assert not ok and "trust warnings" in reason

    # expired verification blocks
    jid3 = _mk_job(env, "3", score=85, verification_state="expired",
                   eligibility_json='{"status": "eligible"}')
    ok, reason = queue.can_apply(jid3)
    assert not ok and "expired" in reason

    # low score blocks
    jid4 = _mk_job(env, "4", score=40, verification_state="verified_open",
                   eligibility_json='{"status": "eligible"}')
    ok, reason = queue.can_apply(jid4)
    assert not ok and "match score" in reason

    # not eligible blocks
    jid5 = _mk_job(env, "5", score=90, verification_state="verified_open",
                   eligibility_json='{"status": "not_eligible"}')
    ok, reason = queue.can_apply(jid5)
    assert not ok and "eligibility" in reason

    # already applied blocks
    db.record_submission(jid, "manual", {}, confirmed=True)
    ok, reason = queue.can_apply(jid)
    assert not ok and "recorded" in reason


def test_daily_limit_blocks(env):
    jid = _mk_job(env, score=85, verification_state="verified_open", status="scored",
                  eligibility_json='{"status": "eligible"}')
    for i in range(15):
        x = _mk_job(env, f"limit{i}", company="Other")
        db.record_submission(x, "manual", {}, confirmed=True)
    ok, reason = queue.can_apply(jid)
    assert not ok and "daily limit" in reason


def test_company_limit_blocks(env):
    y = _mk_job(env, "cap1", company="Acme", score=85, verification_state="verified_open",
                status="scored", eligibility_json='{"status": "eligible"}')
    db.record_submission(y, "manual", {}, confirmed=True)
    db.record_submission(_mk_job(env, "cap2", company="Acme"), "manual", {}, confirmed=True)
    z = _mk_job(env, "cap3", company="Acme", score=85, verification_state="verified_open",
                status="scored", eligibility_json='{"status": "eligible"}')
    ok, reason = queue.can_apply(z)
    assert not ok and "company limit" in reason


def test_queue_rows_show_all_phase17_columns(env):
    from datetime import date, timedelta
    jid = _mk_job(env, score=88, salary_text="₹6-9 LPA",
                  deadline=(date.today() + timedelta(days=2)).isoformat(),
                  verification_state="verified_open", quality_score=80,
                  eligibility_json='{"status": "eligible"}', queue_state="ready",
                  resume_path="/tmp/x.pdf")
    queue.set_queue_state(jid, "ready")
    rows = queue.queue_rows(states=["ready"])
    r = next(x for x in rows if x["job_id"] == jid)
    for col in ("company", "title", "salary", "location", "eligibility", "match_score",
                "quality_score", "deadline", "resume_ready", "queue_state",
                "verification_state", "apply_allowed"):
        assert col in r, col
    assert r["eligibility"] == "eligible"
    assert r["resume_ready"] is True
    assert r["deadline_urgency"] >= 1


def test_action_analyze_moves_to_eligible_or_manual(env, monkeypatch):
    jid = _mk_job(env)
    # stub the verification engine's network fetch
    from jobagent import verify as ve
    class FakeResult:
        state, quality_score = "verified_open", 75
    monkeypatch.setattr(ve, "verify_job", lambda jid: FakeResult())
    db.update(jid, score=80, prefit=80, eligibility_json='{"status": "eligible"}')
    out = queue.action_analyze(jid)
    assert out["queue_state"] == "eligible"

    jid2 = _mk_job(env, "low")
    db.update(jid2, score=30, prefit=30)
    out2 = queue.action_analyze(jid2)
    assert out2["queue_state"] == "needs_manual"


def test_action_approve_requires_authorized_config(env):
    jid = _mk_job(env, score=85, verification_state="verified_open", status="scored",
                  eligibility_json='{"status": "eligible"}', queue_state="shortlisted")
    out = queue.action_approve(jid)
    assert out["ok"] is False and "authorized_submission" in out["error"]


# ---------------------------------------------------------------- execution modes

def test_mode_c_disabled_by_default(env):
    ok, why = execution.authorized_allowed()
    assert not ok
    assert "OFF" in why or "disabled" in why.lower()


def test_mode_c_preflight_requires_approval_state(env):
    jid = _mk_job(env, score=85, verification_state="verified_open", status="scored",
                  eligibility_json='{"status": "eligible"}', queue_state="ready")
    # even with the flag on, a non-approved queue state blocks
    out = execution.mode_c_preflight(jid)
    assert out["ok"] is False
    assert "awaiting_approval" in out["error"] or "authorized" in out["error"]


def test_sensitive_fields_detection(env):
    hits = execution.sensitive_fields([
        {"label": "Aadhaar number"}, {"label": "Full name"}, {"label": "PAN Card"},
        {"label": "I authorize a background check"}, {"label": "Email address"},
        {"label": "Caste (for reservation)"}, {"label": "Years of experience"},
    ])
    assert "Aadhaar number" in hits
    assert "PAN Card" in hits
    assert "I authorize a background check" in hits
    assert "Caste (for reservation)" in hits
    assert "Full name" not in hits and "Email address" not in hits


def test_confirmation_detection(env):
    assert execution.confirm_from_text("Thank you for applying to Acme!")
    assert execution.confirm_from_text("Your application has been received.")
    assert execution.confirm_from_text("random body text") is None


def test_manual_submission_records_evidence(env):
    jid = _mk_job(env, queue_state="applying")
    out = execution.record_manual_submission(jid, "submitted via greenhouse portal")
    assert out["ok"] is True
    subs = db.submissions_for(jid)
    assert len(subs) == 1 and subs[0]["confirmed"] == 1
    assert json.loads(subs[0]["evidence"])["note"].startswith("submitted")
    job = db.get(jid)
    assert job["status"] == "applied" and job["queue_state"] == "submitted"


def test_authorized_result_no_confirmation_needs_manual(env):
    jid = _mk_job(env)
    queue.set_queue_state(jid, "applying")
    out = execution.record_authorized_result(
        jid, confirmed=False, final_url="https://x/apply", screenshot="/tmp/s.png",
        page_text="", stopped_reason="CAPTCHA detected")
    assert out["ok"] is False
    job = db.get(jid)
    assert job["queue_state"] == "needs_manual"
    assert job["status"] != "applied", "must NEVER be marked applied without confirmation"


def test_authorized_result_confirmed_marks_applied(env):
    jid = _mk_job(env)
    queue.set_queue_state(jid, "applying")
    execution.record_authorized_result(
        jid, confirmed=True, final_url="https://x/done", screenshot="/tmp/s.png",
        page_text="Thank you for applying!")
    job = db.get(jid)
    assert job["status"] == "applied" and job["queue_state"] == "submitted"
    subs = db.submissions_for(jid)
    assert json.loads(subs[0]["evidence"])["confirmation_text"].startswith("Thank you for applying")


def test_mode_b_records_session_and_spawns_task(env, monkeypatch):
    jid = _mk_job(env, queue_state="ready")
    spawned = {}
    monkeypatch.setattr(execution, "start_task",
                        lambda label, *args: spawned.update({"label": label, "args": args}))
    out = execution.mode_b(jid)
    assert out["ok"] is True
    assert spawned["args"][0] == "apply-one"
    sess = db.load_session(jid)
    assert sess and sess["mode"] == "assisted"
    assert db.get(jid)["queue_state"] == "applying"
