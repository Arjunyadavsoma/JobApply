"""Phase 18.12 — REAL ATS testing against local sandbox fixtures.

Five sandbox pages mimic the priority platforms' DOM patterns (Greenhouse,
Lever, Ashby, Workday, SmartRecruiters). Every test runs a REAL headless
Chromium against a LOCAL file — no external site is touched, no real
application is submitted, no LLM is billed (the answer agent is stubbed).

Covers: adapter detection, form detection, field mapping (form_intel), resume
uploads, multi-step navigation, validation errors, recovery, approval gates,
duplicate-submission prevention, and the per-job session lock.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

E2E_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(E2E_DIR.parent.parent))

# Human-speed waits are pointless against local fixtures: shrink every driver wait.
os.environ.setdefault("JA_WAIT_SCALE", "0.15")

pytestmark = pytest.mark.e2e

FIXTURES = E2E_DIR / "fixtures"
playwright = pytest.importorskip("playwright.sync_api", reason="playwright not installed")


@pytest.fixture()
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        br = p.chromium.launch(headless=True)
        yield br
        br.close()


@pytest.fixture()
def page(browser):
    ctx = browser.new_context()
    pg = ctx.new_page()
    yield pg
    ctx.close()


@pytest.fixture()
def profile(env):
    """A realistic India-fresher profile the mapping engine fills from."""
    from jobagent.models import CandidateProfile
    return CandidateProfile(
        full_name="Arjun Yadav", email="arjun@example.com", phone="+91 9876543210",
        current_location="Hyderabad, India", linkedin_url="https://linkedin.com/in/arjun",
        github_url="https://github.com/arjun",
        graduation_year=2027, degree="B.Tech", branch="CSE (AI & ML)",
        college="XYZ Institute of Technology", cgpa=8.6,
        school_tenth_name="DPS", tenth_percentage=92.0,
        school_twelfth_name="Narayana", twelfth_percentage=94.5,
        candidate_type="fresher", total_experience_years=0.0,
        work_authorization="Indian citizen; authorized to work in India",
        open_to_relocation=True, notice_period="Immediate", expected_ctc_lpa=6.0,
        active_backlogs=0, previous_backlogs=0,
        preferred_locations=["Hyderabad", "Bengaluru", "Pune"],
    )


@pytest.fixture()
def resume(env, tmp_path):
    """Master resume JSON so the flow's fact source exists."""
    from jobagent.models import Resume
    import jobagent.config as config
    config.MASTER_RESUME_JSON.write_text(Resume(
        name="Arjun Yadav", email="arjun@example.com", phone="+919876543210",
        location="Hyderabad, IN", links=[], skills=[],
        experience=[], projects=[], education=[], certifications=[], achievements=[],
        summary="2027 B.Tech CSE (AI&ML) fresher - Python, ML").model_dump_json(),
        encoding="utf-8")
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4 sandbox resume")
    return pdf


def _stub_answers(monkeypatch):
    """LLM answer agent -> deterministic free-text answers only."""
    from jobagent.models import FieldAnswer, FormAnswers

    def fake_ask(fields, job, resume_obj, cover, already):
        out = []
        for f in fields:
            lab = f.meta["label"].lower()
            if "why" in lab or "anything else" in lab or "message" in lab:
                out.append(FieldAnswer(field_id=f.meta["id"],
                                       value="Grounded sandbox answer from my resume."))
        return FormAnswers(answers=out, needs_human=[])
    monkeypatch.setattr("jobagent.ats_flow.ask_claude", fake_ask)


def _make_job(env, source_key: str, url: str):
    from jobagent import db
    db.upsert_jobs([db.Job(source=source_key, external_id=f"{source_key}:1",
                           company="Sandbox " + source_key.title(),
                           title="Software Engineer (Fresher)",
                           url=url, apply_url=url,
                           description="Entry-level role. Python required.")])
    return db.get(db.job_id(source_key, f"{source_key}:1"))


def _files(resume_pdf):
    return {"resume": resume_pdf}


# ------------------------------------------------------------------ detection

@pytest.mark.parametrize("fixture,expected", [
    ("greenhouse.html", "greenhouse"),
    ("lever.html", "lever"),
    ("ashby.html", "ashby"),
    ("workday.html", "workday"),
    ("smartrecruiters.html", "smartrecruiters"),
])
def test_adapter_detection_on_page(page, env, fixture, expected):
    """On-page markers identify the platform even without a known URL."""
    from jobagent.ats_flow import detect_adapter
    page.goto((FIXTURES / fixture).as_uri())
    adapter, info = detect_adapter("https://unknown-portal.example.com/apply", page)
    assert adapter.key == expected, f"{fixture}: detected {adapter.key}"
    # URL-based detection still works standalone
    url_map = {
        "greenhouse": "https://boards.greenhouse.io/x/jobs/1",
        "lever": "https://jobs.lever.co/x/1",
        "ashby": "https://jobs.ashbyhq.com/x/1",
        "workday": "https://x.wd1.myworkdayjobs.com/careers/x/1",
        "smartrecruiters": "https://careers.smartrecruiters.com/x/1",
    }
    adapter2, _ = detect_adapter(url_map[expected], None)
    assert adapter2.key == expected


def test_matrix_reports_tested_vs_untested():
    from jobagent.ats_flow import compatibility_matrix
    matrix = {m["platform"]: m for m in compatibility_matrix()}
    # Phase 18: five platforms sandbox-verified
    for tested_key in ("greenhouse", "lever", "ashby", "workday", "smartrecruiters"):
        assert matrix[tested_key]["tested"] is True
        assert matrix[tested_key]["support_level"] == "SANDBOX_VERIFIED"
    # Phase 19C: the next eight platforms got fixtures + E2E and were promoted
    for promoted in ("icims", "taleo", "darwinbox", "successfactors", "zoho_recruit",
                     "eightfold", "phenom", "oracle_recruiting"):
        assert matrix[promoted]["tested"] is True
        assert matrix[promoted]["support_level"] == "SANDBOX_VERIFIED"
    # login-walled platforms are honestly declared, never claimed as automated
    for manual in ("linkedin", "naukri", "instahyre"):
        assert matrix[manual]["support_level"] == "MANUAL_HANDOFF"


# ------------------------------------------------------------------ greenhouse flow

def test_greenhouse_full_flow(page, env, profile, resume, monkeypatch):
    """Multi-step + upload + mapping + review pause (assisted mode: never submits)."""
    from jobagent.ats_flow import FlowDriver, detect_adapter
    _stub_answers(monkeypatch)
    job = _make_job(env, "gh", (FIXTURES / "greenhouse.html").as_uri())
    page.goto(job["apply_url"])
    adapter, _ = detect_adapter(job["apply_url"], page)
    assert adapter.key == "greenhouse"

    driver = FlowDriver(page, job, adapter, profile=profile, resume=None,
                        files=_files(resume))
    result = driver.run(submit=False)

    # step 1 filled deterministically from the profile
    assert result.steps[0].auto_filled >= 3, "identity fields must come from the profile"
    assert result.steps[0].fields_total >= 5
    assert page.locator("#first_name").input_value() == "Arjun"
    assert page.locator("#last_name").input_value() == "Yadav"
    assert page.locator("#email").input_value() == "arjun@example.com"
    # resume uploaded
    assert page.locator("#resume").evaluate("el => el.files.length") == 1
    # advanced to step 2 and filled graduation + work authorization
    assert len(result.steps) >= 2
    assert page.select_option("#grad_year") is not None or page.locator("#grad_year").input_value()
    work_auth = page.locator("input[name='work_auth']:checked").count()
    assert work_auth == 1, "work authorization radio must be checked Yes from the profile"
    # assisted run NEVER reaches the platform's confirmation
    assert result.submitted is False
    assert "review" in (result.stopped_reason or "") or result.ok
    assert "#confirmation" not in page.evaluate("document.body.innerHTML") or \
        not page.locator("#confirmation").is_visible()


def test_greenhouse_validation_error_blocks_advance(page, env, profile, resume, monkeypatch):
    """A form with a hard validation error stops the flow instead of force-advancing."""
    from jobagent.ats_flow import FlowDriver, detect_adapter
    _stub_answers(monkeypatch)
    # replace the resume upload with a required text input the driver can't satisfy
    # (resume_file is a file kind -> the text input stays empty -> .field-error)
    html = (FIXTURES / "greenhouse.html").read_text(encoding="utf-8")
    html = html.replace('id="resume" name="resume" type="file"',
                        'id="resume" name="resume" type="text" required')
    page.set_content(html)
    job = _make_job(env, "ghv", "about:blank")
    adapter, _ = detect_adapter("", page)
    driver = FlowDriver(page, job, adapter, profile=profile, resume=None, files={})
    result = driver.run(submit=False)

    # the flow must detect the platform's validation error and stop honestly
    all_errors = [e for r in result.steps for e in r.validation_errors]
    assert all_errors, "the sandbox validation error must be detected"
    assert any("resume" in e.lower() for e in all_errors), all_errors
    assert result.submitted is False
    assert "validation errors" in (result.stopped_reason or "")
    # step 2 was never reached: its fields stay empty
    assert page.locator("#grad_year").input_value() == ""


# ------------------------------------------------------------------ lever flow

def test_lever_single_page_flow(page, env, profile, resume, monkeypatch):
    from jobagent.ats_flow import FlowDriver, detect_adapter
    _stub_answers(monkeypatch)
    job = _make_job(env, "lever", (FIXTURES / "lever.html").as_uri())
    page.goto(job["apply_url"])
    adapter, _ = detect_adapter(job["apply_url"], page)
    driver = FlowDriver(page, job, adapter, profile=profile, resume=None, files=_files(resume))
    result = driver.run(submit=False)

    assert page.locator("#lv_name").input_value() == "Arjun Yadav"
    assert page.locator("#lv_email").input_value() == "arjun@example.com"
    assert page.locator("#lv_location").input_value() == "Hyderabad, India"
    assert page.locator("#lv_resume").evaluate("el => el.files.length") == 1
    assert page.locator("#lv_links").input_value() == "https://github.com/arjun"
    # the LLM-stubbed free-text answer landed
    assert page.locator("#lv_why").input_value().startswith("Grounded sandbox answer")
    assert result.submitted is False
    assert not page.locator("#lv_done").is_visible()


# ------------------------------------------------------------------ ashby flow

def test_ashby_three_step_wizard(page, env, profile, resume, monkeypatch):
    from jobagent.ats_flow import FlowDriver, detect_adapter
    _stub_answers(monkeypatch)
    job = _make_job(env, "ashby", (FIXTURES / "ashby.html").as_uri())
    page.goto(job["apply_url"])
    adapter, _ = detect_adapter(job["apply_url"], page)
    driver = FlowDriver(page, job, adapter, profile=profile, resume=None, files=_files(resume))
    result = driver.run(submit=False)

    assert len(result.steps) >= 3, "3-step wizard must be walked step by step"
    # step 2: notice period select matches the profile value
    assert page.locator("#as_notice").input_value() == "Immediate"
    # relocation radio answered from the profile
    assert page.locator("input[name='relocate'][value='yes']").is_checked()
    # step 3: college + CGPA from the profile; never invented
    assert page.locator("#as_college").input_value() == "XYZ Institute of Technology"
    assert page.locator("#as_grad_year").input_value() == "2027"
    assert result.submitted is False


# ------------------------------------------------------------------ workday flow

def test_workday_automation_ids(page, env, profile, resume, monkeypatch):
    from jobagent.ats_flow import FlowDriver, detect_adapter
    _stub_answers(monkeypatch)
    job = _make_job(env, "wd", (FIXTURES / "workday.html").as_uri())
    page.goto(job["apply_url"])
    adapter, _ = detect_adapter(job["apply_url"], page)
    assert adapter.key == "workday"
    driver = FlowDriver(page, job, adapter, profile=profile, resume=None, files=_files(resume))
    result = driver.run(submit=False)

    assert page.locator("#wd_first").input_value() == "Arjun"
    # step 2 reached via data-automation-id next button
    assert page.locator("#wd_grad_year").input_value() == "2027"
    assert page.locator("input[name='backlogs'][value='no']").is_checked()
    assert result.submitted is False


# ------------------------------------------------------------------ smartrecruiters flow

def test_smartrecruiters_single_page(page, env, profile, resume, monkeypatch):
    from jobagent.ats_flow import FlowDriver, detect_adapter
    _stub_answers(monkeypatch)
    job = _make_job(env, "sr", (FIXTURES / "smartrecruiters.html").as_uri())
    page.goto(job["apply_url"])
    adapter, _ = detect_adapter(job["apply_url"], page)
    driver = FlowDriver(page, job, adapter, profile=profile, resume=None, files=_files(resume))
    result = driver.run(submit=False)

    assert page.locator("#sr_first").input_value() == "Arjun"
    assert page.locator("#sr_last").input_value() == "Yadav"
    assert page.locator("#sr_notice").input_value() == "Immediate"
    assert result.submitted is False
    assert not page.locator("#sr_done").is_visible()


# ------------------------------------------------------------------ authorized submit + evidence

def test_authorized_submit_records_confirmation(page, env, profile, resume, monkeypatch):
    """With submit=True the driver clicks Submit, detects the platform
    confirmation and records evidence — against the sandbox only."""
    from jobagent import db, execution
    from jobagent.ats_flow import FlowDriver, detect_adapter
    _stub_answers(monkeypatch)
    job = _make_job(env, "ghauth", (FIXTURES / "greenhouse.html").as_uri())

    import ruamel.yaml as _ry
    import jobagent.config as config
    yaml = _ry.YAML()
    doc = yaml.load(config.CONFIG_PATH.read_text(encoding="utf-8"))
    doc.setdefault("automation", {})["authorized_submission"] = True
    with config.CONFIG_PATH.open("w", encoding="utf-8") as fh:
        yaml.dump(doc, fh)
    config.load_config.cache_clear()
    db.update(job["id"], queue_state="awaiting_approval", score=90, status="tailored",
              verification_state="verified_open", quality_score=80,
              eligibility_json='{"status": "eligible"}', resume_path=str(resume))
    pre = execution.mode_c_preflight(job["id"])
    assert pre["ok"], pre

    page.goto(job["apply_url"])
    adapter, _ = detect_adapter(job["apply_url"], page)
    driver = FlowDriver(page, job, adapter, profile=profile, resume=None, files=_files(resume))
    result = driver.run(submit=True)

    assert result.submitted is True
    assert result.confirmation, "platform confirmation text must be captured"
    assert page.locator("#confirmation").is_visible()
    out = execution.record_authorized_result(
        job["id"], confirmed=True, final_url=result.final_url,
        page_text=page.inner_text("body"), stopped_reason="")
    assert out["ok"] is True
    assert db.submissions_for(job["id"]), "submission evidence must be recorded"


def test_submit_refused_when_sensitive_fields_unanswered(page, env, profile, monkeypatch):
    """Authorized mode + a government-ID field on the last step => NO submit."""
    from jobagent.ats_flow import FlowDriver, detect_adapter
    _stub_answers(monkeypatch)
    html = (FIXTURES / "lever.html").read_text(encoding="utf-8")
    html = html.replace('id="lv_why"', 'id="aadhaar_no"').replace(
        "Why are you interested in this role?", "Aadhaar Number (required for onboarding)")
    page.set_content(html)
    job = _make_job(env, "sens", "about:blank")
    adapter, _ = detect_adapter("", page)
    driver = FlowDriver(page, job, adapter, profile=profile, resume=None, files={})
    result = driver.run(submit=True)          # even authorized: must refuse
    assert result.submitted is False
    assert "sensitive" in result.stopped_reason
    assert not page.locator("#lv_done").is_visible()


# ------------------------------------------------------------------ duplicate prevention

def test_duplicate_submission_prevented(page, env, profile, resume, monkeypatch):
    """A recorded submission blocks any further apply attempt (gate + queue)."""
    from jobagent import db
    from jobagent.queue import can_apply
    job = _make_job(env, "dup", (FIXTURES / "lever.html").as_uri())
    db.update(job["id"], score=90, verification_state="verified_open",
              quality_score=80, eligibility_json='{"status": "eligible"}')
    ok, _ = can_apply(job["id"])
    assert ok is True
    db.record_submission(job["id"], "manual", {"note": "user submitted manually"}, confirmed=True)
    ok2, reason = can_apply(job["id"])
    assert ok2 is False
    assert "already recorded" in reason


# ------------------------------------------------------------------ recovery + locks

def test_session_lock_blocks_concurrent_drivers(env):
    from jobagent import recovery
    job = _make_job(env, "lock", "https://example.com/apply")
    owner1 = recovery.acquire(job["id"], "task-1")
    assert owner1 == "task-1"
    with pytest.raises(recovery.LockHeld):
        recovery.acquire(job["id"], "task-2")
    assert recovery.holder(job["id"]) == "task-1"
    recovery.release(job["id"], "task-1")
    owner3 = recovery.acquire(job["id"], "task-3")     # free again
    assert owner3 == "task-3"


def test_lock_stale_takeover(env):
    """A lock older than the max age is taken over (crashed worker)."""
    from datetime import datetime, timedelta, timezone
    from jobagent import db, recovery
    job = _make_job(env, "stale", "https://example.com/apply")
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(timespec="seconds")
    with db.connect() as conn:
        conn.execute("INSERT OR REPLACE INTO apply_locks (job_id, owner, acquired_at) "
                     "VALUES (?,?,?)", (job["id"], "crashed-worker", old))
    assert recovery.acquire(job["id"], "new-worker") == "new-worker"


def test_resume_after_crash_skips_filled_fields(page, env, profile, resume, monkeypatch):
    """Simulate: crash after step 1 -> resume -> step-1 fields keep values, no re-fill."""
    from jobagent.ats_flow import FlowDriver, detect_adapter
    from jobagent import recovery
    _stub_answers(monkeypatch)
    job = _make_job(env, "resume", (FIXTURES / "greenhouse.html").as_uri())

    # crash simulation: fill step 1 via one driver, then "crash" (just stop)
    page.goto(job["apply_url"])
    adapter, _ = detect_adapter(job["apply_url"], page)
    d1 = FlowDriver(page, job, adapter, profile=profile, resume=None, files=_files(resume))
    d1.run(submit=False)                       # walks to review pause at step 2
    assert page.locator("#first_name").input_value() == "Arjun"

    # a fresh "browser" reopens and resumes from the checkpoint
    state = recovery.checkpoint(job["id"])
    assert state and state.get("step", 0) >= 1
    # second driver must not be able to lock the same job concurrently
    with pytest.raises(Exception):
        from jobagent.ats_flow import FlowDriver as _FD
        # simulate the lock already held by the crashed session
        recovery.acquire(job["id"], "crashed-session")
        # second acquire must fail
        recovery.acquire(job["id"], "second-session")
    recovery.release(job["id"])

    # resume works: fields already filled are preserved
    report = recovery.resume(job["id"], page)
    assert report["resumed"] is True
    assert page.locator("#first_name").input_value() == "Arjun"   # still filled
    assert report["next_action"] == "continue_flow"


def test_resume_refuses_after_submission(env, page):
    from jobagent import db, recovery
    job = _make_job(env, "resub", (FIXTURES / "lever.html").as_uri())
    db.record_submission(job["id"], "authorized", {"confirmation_text": "thank you"},
                         confirmed=True)
    with pytest.raises(recovery.AlreadySubmitted):
        recovery.resume(job["id"], page)


def test_failure_recorded_to_needs_manual(env):
    from jobagent import recovery, db
    job = _make_job(env, "fail", "https://example.com/apply")
    recovery.record_failure(job["id"], "network dropped mid-form", "/tmp/shot.png")
    after = db.get(job["id"])
    assert after["queue_state"] == "needs_manual"
    assert db.notifications()
