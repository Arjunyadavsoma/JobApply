"""Phase 18 — new API surface tests (offline; LLM-backed routes stubbed)."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(env, monkeypatch):
    monkeypatch.delenv("DASHBOARD_TOKEN", raising=False)
    from jobagent import server
    return TestClient(server.app)


@pytest.fixture()
def job(env, tmp_path):
    from jobagent import db
    db.upsert_jobs([db.Job(
        source="test", external_id="api:1", company="API Corp", title="Python Developer",
        url="https://example.com/1", apply_url="https://jobs.lever.co/api/1",
        description="Python, SQL role. 2027 batch welcome.", location="Hyderabad")])
    jid = db.job_id("test", "api:1")
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 " + b"z" * 20000)
    (tmp_path / "resume.json").write_text(json.dumps({
        "resume": {"summary": "Python SQL developer", "skills": [], "experience": [],
                   "projects": []}}), encoding="utf-8")
    db.update(jid, score=85, prefit=80, verification_state="verified_open",
              eligibility_json='{"status": "eligible"}',
              match_json='{"missing_skills": [], "missing_requirements": []}',
              resume_path=str(resume), quality_score=70)
    return jid


def test_ats_matrix(client):
    r = client.get("/api/ats/matrix")
    assert r.status_code == 200
    matrix = {m["platform"]: m for m in r.json()}
    assert len(matrix) == 13                    # every non-generic platform
    assert matrix["greenhouse"]["tested"] is True
    assert matrix["icims"]["tested"] is False
    assert "best-effort" in matrix["icims"]["status"]


def test_readiness_endpoint(client, job):
    r = client.get(f"/api/jobs/{job}/readiness")
    assert r.status_code == 200
    body = r.json()
    # honest scoring: no cover letter / master resume in this env costs points
    assert body["overall"] >= 70
    assert body["submission_ready"] is True
    assert set(body["dimensions"]) == {
        "candidate_eligibility", "resume_relevance", "skill_coverage", "resume_formatting",
        "keyword_alignment", "screening_answers", "required_fields", "factual_consistency",
        "url_validity"}
    r404 = client.get("/api/jobs/doesnotexist/readiness")
    assert r404.status_code == 404


def test_prepare_and_preview_endpoints(client, job):
    r = client.post(f"/api/jobs/{job}/prepare")
    assert r.status_code == 200
    body = r.json()
    assert len(body["steps"]) == 13
    assert body["ready"] is True

    pv = client.get(f"/api/jobs/{job}/preview")
    assert pv.status_code == 200
    preview = pv.json()
    assert preview["job"]["company"] == "API Corp"
    assert preview["resume_path"]
    assert preview["approval_required"] is True


def test_company_intel_endpoint(client, job, monkeypatch):
    from jobagent.company_intel import CompanyIntel

    def fake_structured(schema, system=None, content=None, effort="medium", task="", **kw):
        return CompanyIntel(overview="Tooling company.",
                            facts_verified=["posting: Python required"],
                            ai_interpretations=["product-led, probably"])

    import jobagent.llm as llm
    monkeypatch.setattr(llm, "structured", fake_structured)  # local imports pick this up

    r = client.get(f"/api/jobs/{job}/company-intel")
    assert r.status_code == 200
    body = r.json()
    assert body["intel"]["overview"] == "Tooling company."
    assert body["intel"]["facts_verified"] == ["posting: Python required"]
    # second call serves the cache
    r2 = client.get(f"/api/jobs/{job}/company-intel")
    assert r2.status_code == 200


def test_batch_endpoints(client, job, monkeypatch):
    from jobagent import prep as prep_module
    monkeypatch.setattr(prep_module, "prepare",
                        lambda jid, skip_llm=False: {"ready": True,
                                                     "readiness": {"overall": 88}})
    r = client.post("/api/batch/prepare", json={
        "job_ids": [job], "label": "api-batch", "excluded_companies": ["other corp"]})
    assert r.status_code == 200
    body = r.json()
    assert body["prepared"] == 1 and body["results"][0]["ok"] is True

    h = client.get("/api/batch/history")
    assert h.status_code == 200
    assert h.json()[0]["label"] == "api-batch"


def test_interview_prep_endpoints(client, job, monkeypatch):
    from jobagent.interview_prep import PrepQuestion

    def fake_structured(schema, system=None, content=None, effort="high", task="", **kw):
        qs = [PrepQuestion(id=f"q_{c}", category=c, question=f"{c}?",
                           explanation="why", model_answer="grounded")
              for c in ("company", "python", "sql")]
        return schema(company="API Corp", role="Python Developer", questions=qs,
                      focus_areas=["Python"], talking_points=[])

    import jobagent.interview_prep as ip
    import jobagent.llm as llm
    monkeypatch.setattr(llm, "structured", fake_structured)  # local import in plan()

    r = client.get(f"/api/jobs/{job}/interview-prep")
    assert r.status_code == 200
    body = r.json()
    assert len(body["plan"]["questions"]) == 3
    assert body["progress"]["total_questions"] == 3

    m = client.post(f"/api/jobs/{job}/interview-prep/mock", json={"category": "python"})
    assert m.status_code == 200
    assert m.json()["question"]["id"] == "q_python"

    a = client.post(f"/api/jobs/{job}/interview-prep/answer",
                    json={"question_id": "q_python", "answered": True})
    assert a.status_code == 200
    assert "model_answer" in a.json()
    assert a.json()["progress"]["attempted"]["python"]["answered"] == 1

    # unknown question -> 404
    a404 = client.post(f"/api/jobs/{job}/interview-prep/answer",
                       json={"question_id": "nope", "answered": True})
    assert a404.status_code == 404


def test_agent_history_endpoint(client, job):
    from jobagent import agents
    r = client.get("/api/agents/history")
    assert r.status_code == 200
    # empty history is fine (no runs yet in this env)
    assert isinstance(r.json(), list)


def test_resume_session_endpoint_blocks_duplicate(client, job):
    from jobagent import db
    db.record_submission(job, "manual", {"note": "sent"}, confirmed=True)
    r = client.post(f"/api/jobs/{job}/apply/resume-session")
    assert r.status_code == 409
    assert "duplicate" in r.json()["detail"].lower()


def test_resume_session_endpoint_lock_conflict(client, job, monkeypatch):
    from jobagent import recovery
    recovery.acquire(job, "running-worker")

    # patch the browser away: the lock check must happen before any browser use
    r = client.post(f"/api/jobs/{job}/apply/resume-session")
    assert r.status_code == 409
    assert "running-worker" in r.json()["detail"]
    recovery.release(job)
