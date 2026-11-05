"""Phase 17.10 — E2E: the real form engine (Playwright + Chromium) against a LOCAL
sandbox application form. No external site is touched, nothing is submitted, no
LLM is billed (answers come from a stubbed FormAnswers payload).

Covers: semantic field extraction (labels/aria, not CSS selectors), text/select/
radio/checkbox filling, file upload, multi-step navigation, sensitive-field
detection, CAPTCHA detection, session checkpoint persistence.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

E2E_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(E2E_DIR.parent.parent))

pytestmark = pytest.mark.e2e

FIXTURE = E2E_DIR / "fixtures" / "ats_form.html"

playwright = pytest.importorskip("playwright.sync_api", reason="playwright not installed")


@pytest.fixture()
def page():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context()
        pg = ctx.new_page()
        pg.goto(FIXTURE.as_uri())
        yield pg
        ctx.close()
        browser.close()


@pytest.fixture()
def job(env):
    # master resume so autofill's fact source exists
    from jobagent.models import Resume
    import jobagent.config as config
    config.MASTER_RESUME_JSON.write_text(Resume(
        name="Cand One", email="cand@example.com", phone="+919999888877",
        location="Hyderabad, IN", links=[], skills=[], experience=[], projects=[],
        education=[], certifications=[], achievements=[],
        summary="Fresher - Python, ML").model_dump_json(), encoding="utf-8")
    from jobagent import db
    db.upsert_jobs([db.Job(source="sandbox", external_id="sandbox:1", company="Sandbox Labs",
                           title="Software Engineer (Fresher)",
                           url=FIXTURE.as_uri(), apply_url=FIXTURE.as_uri(),
                           description="Entry-level role. Python required.")])
    return db.get(db.job_id("sandbox", "sandbox:1"))


def _stub_answers(monkeypatch, answers: dict, needs_human: list[str] | None = None):
    """Stub the LLM answer step: returns deterministic FormAnswers for field ids."""
    from jobagent.apply import ask_claude
    from jobagent.models import FieldAnswer, FormAnswers

    def fake_ask(fields, job, resume, cover, already):
        ids = [f.meta["id"] for f in fields]
        return FormAnswers(answers=[FieldAnswer(field_id=i, value=answers[i])
                                    for i in ids if i in answers],
                           needs_human=needs_human or [])
    monkeypatch.setattr("jobagent.apply.ask_claude", fake_ask)


def test_field_extraction_finds_semantic_fields(page, job, env):
    from jobagent.apply import extract_fields
    fields = extract_fields(page, read_options=False)
    labels = {f.meta["label"].lower(): f.meta for f in fields.values()}
    assert any("full name" in l for l in labels), f"labels seen: {list(labels)}"
    assert any("email" in l for l in labels)
    assert any("phone" in l for l in labels)
    assert any("city" in l for l in labels)
    assert any("resume" in l for l in labels)
    # radio group: 2027 batch question with 2 options
    radio = next(f for f in fields.values() if f.meta["kind"] == "radio")
    assert len(radio.meta["options"]) == 2
    # select: 4 city options
    sel = next(f for f in fields.values() if f.meta["kind"] == "select")
    assert "Hyderabad" in sel.meta["options"]


def test_autofill_fills_all_field_types(page, job, env, monkeypatch, tmp_path):
    from jobagent.apply import autofill, extract_fields
    resume_pdf = tmp_path / "resume.pdf"
    resume_pdf.write_bytes(b"%PDF-1.4 minimal")
    _stub_answers(monkeypatch, {
        # stable identity: label-based ids are fr0_0, fr0_1... but stub maps by id list
    })

    # Map answers by LABEL for robustness (ids depend on DOM order but that is stable here)
    def fake_ask(fields, j, resume, cover, already):
        from jobagent.models import FieldAnswer, FormAnswers
        by_label = {}
        for f in fields:
            lab = f.meta["label"].lower()
            if "full name" in lab: by_label[f.meta["id"]] = "Cand One"
            elif "email" in lab: by_label[f.meta["id"]] = "cand@example.com"
            elif "phone" in lab: by_label[f.meta["id"]] = "+919999888877"
            elif "city" in lab: by_label[f.meta["id"]] = "Hyderabad"
            elif "2027" in lab: by_label[f.meta["id"]] = "Yes, 2027 batch"
            elif "experience" in lab: by_label[f.meta["id"]] = "0"
            elif "resume" in lab or "cv" in lab: by_label[f.meta["id"]] = "resume"
            elif "why" in lab: by_label[f.meta["id"]] = "I build AI features with Python."
        return FormAnswers(answers=[FieldAnswer(field_id=k, value=v) for k, v in by_label.items()],
                           needs_human=[])
    monkeypatch.setattr("jobagent.apply.ask_claude", fake_ask)

    autofill(page, job, None, "", {"resume": resume_pdf}, passes=2)

    after = extract_fields(page, read_options=False)
    got = {}
    for f in after.values():
        got[f.meta["label"].lower()] = (f.meta.get("value") or "").strip()
    values = set(got.values())
    assert "Cand One" in values, f"name not filled: {got}"
    email_val = next(v for k, v in got.items() if "email" in k)
    assert email_val == "cand@example.com"
    assert "Hyderabad" in values, f"city not selected: {got}"
    assert any("2027" in v for v in values), f"radio not checked: {got}"
    assert "uploaded" in values or any("uploaded" in str(v) for v in values), f"resume not uploaded: {got}"

    # the apply session was checkpointed (Phase 17.5)
    from jobagent import db
    # autofill records an event
    ev = db.events_for(job["id"])
    assert any(e["kind"] == "apply" for e in ev)


def test_sensitive_fields_detected_on_step2(page, env):
    from jobagent.apply import extract_fields
    from jobagent.execution import sensitive_fields
    page.click("#nextBtn")               # reveal step 2
    page.wait_for_timeout(300)
    fields = extract_fields(page, read_options=False)
    metas = [f.meta for f in fields.values()]
    hits = sensitive_fields(metas)
    assert any("aadhaar" in h.lower() for h in hits), hits
    assert any("authorize" in h.lower() for h in hits), hits
    assert not any("full name" in h.lower() for h in hits)


def test_captcha_detected_never_bypassed(env):
    html = (E2E_DIR / "fixtures" / "ats_form.html").read_text(encoding="utf-8")
    html = html.replace("</form>", '</form><iframe src="https://www.google.com/recaptcha/api2/anchor" title="recaptcha"></iframe>')
    from playwright.sync_api import sync_playwright
    from jobagent.execution import captcha_present
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        pg = browser.new_page()
        pg.set_content(html)
        assert captcha_present(pg) is True
        browser.close()
    # and the clean fixture has none
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        pg = browser.new_page()
        pg.goto(FIXTURE.as_uri())
        assert captcha_present(pg) is False
        browser.close()


def test_authorized_flow_stops_on_sensitive_fields(page, job, env, monkeypatch, tmp_path):
    """MODE C end-to-end against the sandbox: fill step 1, reach step 2, refuse to
    submit because an Aadhaar field + consent checkbox need a human."""
    from jobagent import db, execution, queue
    # enable authorized mode + approve the job
    import ruamel.yaml as _ry
    import jobagent.config as config
    yaml = _ry.YAML()
    doc = yaml.load(config.CONFIG_PATH.read_text(encoding="utf-8"))
    doc.setdefault("automation", {})
    doc["automation"]["authorized_submission"] = True
    with config.CONFIG_PATH.open("w", encoding="utf-8") as fh:
        yaml.dump(doc, fh)
    config.load_config.cache_clear()

    db.update(job["id"], queue_state="awaiting_approval", score=90, status="tailored",
              verification_state="verified_open", quality_score=80,
              eligibility_json='{"status": "eligible"}',
              resume_path=str(tmp_path / "resume.pdf"))
    (tmp_path / "resume.pdf").write_bytes(b"%PDF-1.4")

    # preflight must pass now
    pre = execution.mode_c_preflight(job["id"])
    assert pre["ok"], pre

    # simulate what the CLI worker does on the sensitive step, without spawning a task
    from jobagent.apply import extract_fields
    from jobagent.execution import sensitive_fields
    page.click("#nextBtn")
    page.wait_for_timeout(300)
    metas = [f.meta for f in extract_fields(page, read_options=False).values()]
    assert sensitive_fields(metas), "sandbox step 2 must contain sensitive fields"

    out = execution.record_authorized_result(
        job["id"], confirmed=False, final_url=page.url, page_text="",
        stopped_reason="sensitive fields: " + ", ".join(sensitive_fields(metas)[:2]))
    assert out["ok"] is False
    after = db.get(job["id"])
    assert after["status"] != "applied"
    assert after["queue_state"] == "needs_manual"
    subs = db.submissions_for(job["id"])
    assert len(subs) == 0, "no submission may be recorded without confirmation"
