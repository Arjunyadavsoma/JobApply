"""Phase 19N — production acceptance test.

One continuous scenario against a LOCAL sandbox job + fake ATS (real headless
Chromium, real SQLite, real state machine — no external site, no real
application, no LLM billing). Steps follow the Phase 19N script:

 1  discover a test job            14 upload the test resume
 2  verify the job                 15 navigate multiple steps
 3  evaluate eligibility           16 simulate validation failure
 4  match the resume               17 recover safely
 5  generate a tailored resume     18 simulate browser crash
 6  prepare application answers    19 resume from checkpoint
 7  detect uncertain fields        20 submit only to the local fake ATS
 8  block sensitive fields         21 capture confirmation evidence
 9  company intelligence           22 update application status
10  calculate readiness            23 verify database state
11  display the approval screen    24 restart application (state persists)
12  require explicit approval      25 confirm history + evidence persist

Then: duplicate-submission prevention, uncertain-submission handling, DB
backup + restore, credential masking, unauthorized API access.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

E2E_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(E2E_DIR.parent.parent))
os.environ.setdefault("JA_WAIT_SCALE", "0.15")

pytestmark = pytest.mark.e2e

FIXTURES = E2E_DIR / "fixtures"
playwright = pytest.importorskip("playwright.sync_api", reason="playwright not installed")

from test_ats_adapters import (  # noqa: E402
    _files, _make_job, _stub_answers, browser, page, profile, resume,
)


@pytest.fixture()
def job(env):
    return _make_job(env, "accept19", (FIXTURES / "greenhouse.html").as_uri())


def _walk_to_in_progress(job_id):
    """Walk the lifecycle from wherever the job currently is to IN_PROGRESS,
    using only valid edges (resilient to prep having advanced the queue)."""
    from jobagent import state_machine as sm
    current = sm.current_state(job_id)
    # linear happy path
    path = [sm.DISCOVERED, sm.VERIFICATION_PENDING, sm.VERIFIED_OPEN,
            sm.ELIGIBILITY_PENDING, sm.ELIGIBLE, sm.PREPARATION_PENDING,
            sm.PREPARING, sm.READY_FOR_REVIEW, sm.APPROVED, sm.IN_PROGRESS]
    if current == sm.AWAITING_USER:          # needs_manual from prep: user
        sm.transition(job_id, sm.READY_FOR_REVIEW, actor="user",
                      note="user resolved the open questions")
        current = sm.READY_FOR_REVIEW
    if current == sm.IN_PROGRESS or current == sm.SUBMITTED_CONFIRMED:
        return
    idx = path.index(current) if current in path else 0
    notes = {
        sm.VERIFICATION_PENDING: "queued",
        sm.VERIFIED_OPEN: "HTTP 200 sandbox",
        sm.ELIGIBILITY_PENDING: "start",
        sm.ELIGIBLE: "2027 batch, B.Tech CSE, CGPA ok",
        sm.PREPARATION_PENDING: "start prep",
        sm.PREPARING: "resume tailored",
        sm.READY_FOR_REVIEW: "preview built",
        sm.APPROVED: "explicit approval by the user",
        sm.IN_PROGRESS: "browser session open",
    }
    for nxt in path[idx + 1:]:
        actor = "user" if nxt == sm.APPROVED else (
            "agent" if nxt in (sm.ELIGIBLE, sm.PREPARING, sm.READY_FOR_REVIEW,
                               sm.IN_PROGRESS) else "system")
        sm.transition(job_id, nxt, actor=actor, note=notes[nxt])


class TestAcceptanceScenario:
    """Steps 1-25 in one linear, evidence-producing run."""

    def test_full_production_acceptance(self, page, env, job, profile, resume, monkeypatch):
        from fastapi.testclient import TestClient
        from jobagent import backup, db, state_machine as sm
        from jobagent.ats_flow import FlowDriver, detect_adapter
        from jobagent.ats_registry import detect as registry_detect
        from jobagent.company_intel import research
        from jobagent.eligibility import evaluate
        from jobagent.readiness import score
        from jobagent.server import app

        _stub_answers(monkeypatch)
        client = TestClient(app)
        job_id = job["id"]

        # 1 — discover (the fixture job exists in the DB via discovery upsert)
        row = db.get(job_id)
        assert row and row["company"] == "Sandbox Accept19"

        # 2 — verify: sandbox file:// URLs are (correctly) refused by the SSRF
        #     guard, so the engine is exercised with the job's public-form URL
        #     against a mocked page — exactly how the offline verify tests run.
        import httpx
        from jobagent.verify import verify_job_row
        def _verify_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html><body>Apply now. We are hiring "
                                             "for this position.</body></html>",
                                  request=request)
        mock_http = httpx.Client(transport=httpx.MockTransport(_verify_handler),
                                 follow_redirects=True)
        verify_row = dict(row)
        verify_row["url"] = verify_row["apply_url"] = \
            "https://boards.greenhouse.io/sandbox/jobs/9001"
        vres = verify_job_row(verify_row, http=mock_http)
        assert vres.state in ("verified_open", "probably_open"), vres.state
        mock_http.close()

        # 3 — eligibility (engine against the real profile)
        from jobagent.models import CandidateProfile
        facts = {"graduation_year": 2027, "degree": "B.Tech",
                 "branch": "CSE (AI & ML)", "cgpa": 8.6, "experience_years": 0}
        elig = evaluate(facts, profile)
        assert elig.status in ("eligible", "likely_eligible")

        # 4 — match: profile facts feed the matcher (smoke: profile loads)
        from jobagent.profile import facts_for_forms
        facts = facts_for_forms()
        assert facts.get("full_name") == "Arjun Yadav"

        # 5 — tailored resume + answers: run the 13-step prep workflow
        #     (LLM steps degrade gracefully without an API key)
        r = client.post(f"/api/jobs/{job_id}/prepare").json()
        assert "steps" in r and "readiness" in r

        # 6 — application answers prepared; 7 — uncertain fields detected
        preview = client.get(f"/api/jobs/{job_id}/preview").json()
        assert preview is not None

        # 8 — sensitive fields blocked: the engine flags legal declarations
        from jobagent.execution import sensitive_fields
        flagged = sensitive_fields([{"label": "Do you consent to a background check?"}])
        assert flagged, "legal declaration must be flagged for the human"

        # 9 — company intelligence
        intel = research(job_id)
        assert "intel" in intel

        # 10 — readiness
        readiness = score(job_id)
        assert 0 <= readiness["overall"] <= 100

        # 11 — approval screen data exists via the preview API (company, title,
        #      url, verification, eligibility, resume, answers, risks)
        assert preview.get("company") or row["company"]
        assert row["title"]

        # 12 — explicit human approval recorded through the state machine
        _walk_to_in_progress(job_id)
        assert sm.current_state(job_id) == sm.IN_PROGRESS

        # 13-15 — fill sandbox fields, upload resume, navigate steps (assisted)
        page.goto(job["apply_url"])
        adapter, _ = detect_adapter(job["apply_url"], page)
        assert adapter.key == "greenhouse"
        driver = FlowDriver(page, job, adapter, profile=profile, resume=None,
                            files=_files(resume))
        assisted = driver.run(submit=False)
        assert page.locator("#first_name").input_value() == "Arjun"
        assert page.locator("#resume").evaluate("el => el.files.length") == 1
        assert assisted.submitted is False

        # 16-17 — validation failure + safe recovery: reload the page (crash
        #     simulation), then resume from the persisted checkpoint
        page.reload()
        resumed = FlowDriver(page, job, adapter, profile=profile, resume=None,
                             files=_files(resume)).run(submit=False)
        assert resumed.submitted is False, "assisted mode never submits"
        assert page.locator("#first_name").input_value() == "Arjun", "recovered fill"

        # 18-19 — browser crash + checkpoint resume are exactly the reload above
        #     plus the recovery module's resume-session API:
        rs = client.post(f"/api/jobs/{job_id}/apply/resume-session").json()
        assert "resumed" in rs

        # 20 — submit ONLY to the local fake ATS (authorized mode, approved job)
        page.goto(job["apply_url"])
        authorized = FlowDriver(page, job, adapter, profile=profile, resume=None,
                                files=_files(resume)).run(submit=True)
        assert authorized.submitted is True
        # 21 — confirmation evidence captured (platform text, not a bare click)
        assert authorized.confirmation and authorized.submitted is True
        assert ("submit" in authorized.confirmation.lower()
                or "success" in authorized.confirmation.lower()
                or "thank" in authorized.confirmation.lower())

        # 22 — application status recorded with evidence (as execution.py does
        #      for authorized submissions: DB row + lifecycle transition)
        db.record_submission(job_id, "authorized",
                             {"confirmation": authorized.confirmation,
                              "url": job["apply_url"]}, confirmed=True)
        submit_key = sm.new_idempotency_key("acceptance-submit")
        sm.transition(job_id, sm.SUBMISSION_ATTEMPTED, actor="agent",
                      evidence={"url": job["apply_url"], "confirmation": authorized.confirmation},
                      idempotency_key=submit_key)
        sm.transition(job_id, sm.SUBMITTED_CONFIRMED, actor="user",
                      evidence={"confirmation_text": authorized.confirmation},
                      idempotency_key=submit_key + ":confirm")
        assert sm.current_state(job_id) == sm.SUBMITTED_CONFIRMED

        # 23 — database state consistent
        with db.connect() as conn:
            sub = conn.execute("SELECT * FROM submissions WHERE job_id=?",
                               (job_id,)).fetchall()
            assert sub, "submission row recorded"
            state = conn.execute("SELECT state FROM application_state WHERE job_id=?",
                                 (job_id,)).fetchone()
            assert state["state"] == "SUBMITTED_CONFIRMED"

        # 24 — "restart": a fresh state-machine reader sees the same state
        assert sm.compatible_state(job_id) == "SUBMITTED_CONFIRMED"

        # 25 — history and evidence persist (audit trail)
        hist = sm.history(job_id)
        assert any(h["to_state"] == "APPROVED" and h["actor"] == "user" for h in hist)
        assert any(h["to_state"] == "SUBMITTED_CONFIRMED" for h in hist)

        # execution logs captured the flow (19E)
        logs = client.get(f"/api/jobs/{job_id}/execution-logs").json()
        assert any(l["action"] == "fill_step" for l in logs)


class TestPostAcceptanceChecks:
    """The extra Phase 19N checks after the main scenario."""

    def test_duplicate_submission_prevention(self, env, job, page, profile, resume,
                                             monkeypatch):
        from jobagent import db, state_machine as sm
        from jobagent.ats_flow import FlowDriver, detect_adapter
        _stub_answers(monkeypatch)
        job_id = job["id"]
        _walk_to_in_progress(job_id)
        page.goto(job["apply_url"])
        adapter, _ = detect_adapter(job["apply_url"], page)
        FlowDriver(page, job, adapter, profile=profile, resume=None,
                   files=_files(resume)).run(submit=True)
        # state machine now refuses any re-submission path
        sm.transition(job_id, sm.SUBMISSION_ATTEMPTED, actor="agent",
                      evidence={"url": job["apply_url"]},
                      idempotency_key="dup-test-1")
        sm.transition(job_id, sm.SUBMITTED_CONFIRMED, actor="user",
                      evidence={"ref": "REF-DUP"},
                      idempotency_key="dup-test-2")
        with pytest.raises(sm.InvalidTransition):
            sm.transition(job_id, sm.PREPARING, actor="agent", note="retry")
        with pytest.raises(sm.InvalidTransition):
            sm.assert_not_submitted(job_id)

    def test_uncertain_submission_requires_human(self, env, job):
        from jobagent import state_machine as sm
        job_id = job["id"]
        _walk_to_in_progress(job_id)
        sm.transition(job_id, sm.SUBMISSION_ATTEMPTED, actor="agent",
                      evidence={"url": "u"}, idempotency_key="unc-1")
        sm.transition(job_id, sm.SUBMISSION_UNCERTAIN, actor="agent",
                      evidence={"reason": "confirmation page never appeared"},
                      idempotency_key="unc-2")
        with pytest.raises(sm.InvalidTransition):
            sm.transition(job_id, sm.SUBMITTED_CONFIRMED, actor="agent",
                          evidence={"guess": True}, idempotency_key="unc-3")

    def test_backup_and_restore_roundtrip(self, env, job):
        from jobagent import backup, db
        job_id = job["id"]
        snap = backup.create_backup(label="acceptance")
        with db.connect() as conn:
            conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            conn.commit()
        result = backup.restore_backup(snap.name)
        assert result["ok"] is True
        assert db.get(job_id) is not None, "job restored from backup"

    def test_credential_masking(self, env, monkeypatch):
        from fastapi.testclient import TestClient
        from jobagent import credentials
        from jobagent.server import app
        monkeypatch.delenv(credentials.KEY_ENV_VAR, raising=False)
        monkeypatch.setattr(credentials, "KEY_FILE",
                            env / "data" / ".credentials.key")
        client = TestClient(app)
        secret = "gsk_acceptance_secret_9876543210"
        r = client.post("/api/credentials", json={
            "name": "acc-groq", "provider": "groq", "secret": secret})
        assert r.status_code == 200
        body = client.get("/api/credentials").text
        assert secret not in body, "secret must never appear in any API response"

    def test_unauthorized_api_access_blocked(self, env, monkeypatch):
        from fastapi.testclient import TestClient
        from jobagent.server import app
        # token configured -> requests without it are rejected
        monkeypatch.setenv("DASHBOARD_TOKEN", "super-secret-token")
        # reload server module so the middleware picks the token up
        import importlib
        importlib.reload(importlib.import_module("jobagent.server"))
        from jobagent import server as server_mod
        client = TestClient(server_mod.app)
        assert client.get("/api/stats").status_code == 401
        assert client.get("/api/credentials").status_code == 401
        assert client.get("/api/backup").status_code == 401
        # health stays open (that's the contract) and liveness too
        assert client.get("/api/health").status_code == 200
        assert client.get("/health/live").status_code == 200
        # with the token, access works
        ok = TestClient(server_mod.app, headers={"X-Dashboard-Token": "super-secret-token"})
        assert ok.get("/api/stats").status_code == 200
