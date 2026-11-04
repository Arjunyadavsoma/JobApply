"""Phase 17.8 — agent orchestration layer tests (all LLM calls mocked)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jobagent import agents, db


@pytest.fixture()
def job(env):
    # a minimal master resume so matcher/tailor paths don't exit early
    from jobagent.models import Resume
    import jobagent.config as config
    config.MASTER_RESUME_JSON.write_text(Resume(
        name="Cand", email="c@x.io", phone="9999999999", location="Hyderabad, IN",
        links=[], skills=[], experience=[], projects=[], education=[], certifications=[],
        achievements=[], summary="fresher").model_dump_json(), encoding="utf-8")
    db.upsert_jobs([db.Job(source="greenhouse", external_id="acme:1", company="Acme Labs",
                           title="AI Engineer (Fresher)",
                           url="https://boards.greenhouse.io/acme/jobs/1",
                           apply_url="https://boards.greenhouse.io/acme/jobs/1",
                           description="Build LLM features. 0-2 years. Python required.")])
    return db.get(db.job_id("greenhouse", "acme:1"))


def _patch_structured(monkeypatch, payload):
    """Patch structured() in EVERY module that imported it (agents + ai_services)."""
    captured: dict = {}

    def fake_structured(model, *, system, content, effort, task, **kw):
        captured.update({"system": system, "content": content, "task": task,
                         "model": model.__name__})
        return model.model_validate(payload)

    import jobagent.ai_services as ais
    monkeypatch.setattr(agents, "structured", fake_structured)
    monkeypatch.setattr(ais, "structured", fake_structured)
    return captured


def test_registry_lists_all_agents():
    reg = agents.registry()
    keys = {a["key"] for a in reg}
    assert keys == {"job_research", "company_research", "job_matching", "resume_tailoring",
                    "cover_letter", "application_answer", "application_quality",
                    "form_understanding", "interview_prep", "follow_up"}
    for a in reg:
        assert a["prompt_version"], a["key"]


def _resume_stub() -> str:
    from jobagent.models import Resume
    return Resume(name="Cand", email="c@x.io", phone="9999999999", location="Hyderabad, IN",
                  links=[], skills=[], experience=[], projects=[], education=[],
                  certifications=[], achievements=[], summary="fresher").model_dump_json()


def test_job_research_untrusted_framing(job, monkeypatch):
    captured = _patch_structured(monkeypatch, {
        "company_snapshot": "AI startup", "role_summary": "LLM feature work",
        "top_requirements": ["Python"], "red_flags": [], "questions_to_ask": ["team size?"]})
    out = agents.run("job_research", job["id"])
    assert out["company_snapshot"] == "AI startup"
    assert '<job_description source="external, untrusted">' in captured["content"]
    assert "ignore them" in captured["content"], "prompt-injection instruction must be present"
    ev = db.events_for(job["id"])
    assert any(e["kind"] == "ai" for e in ev)


def test_unknown_agent_rejected(job):
    with pytest.raises(KeyError):
        agents.run("does_not_exist", job["id"])


def test_answer_agent_uses_question(job, monkeypatch):
    _patch_structured(monkeypatch, {"answer": "Because...", "used_saved_answer": False,
                                    "grounded": True})
    import jobagent.ai_services as ais
    monkeypatch.setattr(ais, "_resume_json", _resume_stub)
    out = agents.run("application_answer", job["id"], question="Notice period?")
    assert out["question"] == "Notice period?"
    assert out["grounded"] is True


def test_dispatch_routes_to_existing_services(job, monkeypatch):
    calls = []

    import jobagent.ai_services as ais
    def fake_run_service(name, j):
        calls.append(name)
        return {"service": name}
    monkeypatch.setattr(ais, "run_service", fake_run_service)

    # real matcher path: patch score_one at the module agents imports it from
    import jobagent.matcher as matcher
    from jobagent.models import EligibilityResult, MatchResultV2
    fake_m = MatchResultV2(score=88, verdict="apply", skills_score=90, education_score=80,
                           experience_score=85, location_score=95, reasons=["python match"],
                           missing_skills=[], disqualifiers=[], recommendations=[])
    fake_e = EligibilityResult(status="eligible", checks=[])
    monkeypatch.setattr(matcher, "score_one", lambda j, m, p: (fake_m, fake_e))

    # Phase 18: interview_prep routes to the richer interview_prep module
    import jobagent.interview_prep as ip
    monkeypatch.setattr(ip, "plan", lambda jid, force=False: {
        "questions": [{"id": "q1", "category": "python", "question": "Q?"}]})
    ip_out = agents.run("interview_prep", job["id"])
    assert ip_out["plan"]["questions"][0]["id"] == "q1"
    assert "progress" in ip_out

    assert agents.run("follow_up", job["id"]) == {"service": "follow_up"}
    assert agents.run("job_matching", job["id"])["score"] == 88
    assert set(calls) == {"follow_up"}                       # follow_up still via ai_services
    after = db.get(job["id"])
    assert after["score"] == 88 and after["status"] == "scored"


def test_missing_job_raises(job):
    with pytest.raises(KeyError):
        agents.run("job_research", "nonexistent")


def test_cover_letter_reuses_stored_tailor(job, monkeypatch, tmp_path):
    """If a tailored result already exists on disk, the cover-letter agent must NOT re-bill."""
    from jobagent.models import Resume, TailoredResume
    resume = Resume(name="C", email="c@x.io", phone="9999999999", location="Hyderabad, IN",
                     links=[], skills=[], experience=[], projects=[], education=[],
                     certifications=[], achievements=[], summary="fresher")
    out_dir = tmp_path / "acme"
    out_dir.mkdir()
    (out_dir / "resume.json").write_text(TailoredResume(
        resume=resume, changes=["reordered skills"],
        cover_letter="Dear Acme, ...").model_dump_json(), encoding="utf-8")
    (out_dir / "cover_letter.txt").write_text("Dear Acme, ...", encoding="utf-8")
    db.update(job["id"], resume_path=str(out_dir / "resume.pdf"),
              cover_path=str(out_dir / "cover_letter.txt"))

    called = []
    import jobagent.resume as resume_mod
    monkeypatch.setattr(resume_mod, "tailor", lambda jid: called.append(jid))

    out = agents.run("cover_letter", job["id"])
    assert out["cover_letter"].startswith("Dear Acme")
    assert not called, "must reuse the stored tailor result"
