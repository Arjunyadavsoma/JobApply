"""API tests via FastAPI TestClient - offline, temp data dir, token auth, validation."""
from __future__ import annotations

from io import BytesIO

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(env, monkeypatch):
    monkeypatch.delenv("DASHBOARD_TOKEN", raising=False)
    from jobagent import server
    return TestClient(server.app)


def _make_job(db, n=1, **kw):
    from jobagent.db import Job
    base = dict(source="test", external_id=str(n), company="TestCorp", title="ML Engineer",
                url=f"https://example.com/{n}", description="2027 batch AI role, 8 LPA")
    base.update(kw)
    j = Job(**base)
    db.upsert_jobs([j])
    from jobagent.enrich import apply_to_job, enrich
    apply_to_job(j.id, enrich(j.title, j.description, j.company, j.url))
    return j


def test_health_open_without_auth(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_dashboard_token_auth(env, monkeypatch):
    monkeypatch.setenv("DASHBOARD_TOKEN", "secret123")
    from jobagent import server
    server.DASHBOARD_TOKEN = "secret123"
    c = TestClient(server.app)
    assert c.get("/api/stats").status_code == 401
    assert c.get("/api/health").status_code == 200                       # health stays open
    assert c.get("/api/stats", headers={"X-Dashboard-Token": "secret123"}).status_code == 200
    server.DASHBOARD_TOKEN = ""


def test_stats(client):
    r = client.get("/api/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 0 and "has_profile" in body


def test_profile_roundtrip(client):
    r = client.get("/api/profile")
    assert r.status_code == 200
    p = r.json()["profile"]
    assert p["graduation_year"] == 2027                       # India defaults seeded
    assert client.put("/api/profile", json={"cgpa": 9.1, "graduation_year": 2027}).status_code == 200
    p2 = client.get("/api/profile").json()["profile"]
    assert p2["cgpa"] == 9.1


def test_profile_rejects_garbage(client):
    r = client.put("/api/profile", json={"graduation_year": "not-a-number"})
    assert r.status_code in (400, 422)


def test_jobs_filters(client, env):
    from jobagent import db
    j = _make_job(db)
    assert len(client.get("/api/jobs").json()) == 1
    assert len(client.get("/api/jobs", params={"fresher": 1}).json()) == 1
    assert len(client.get("/api/jobs", params={"fresher": 1, "source": "linkedin"}).json()) == 0
    detail = client.get(f"/api/jobs/{j.id}").json()
    assert detail["salary_min"] == 8.0
    assert detail["events"]                                     # discovered event recorded
    assert client.get("/api/jobs/nonexistent").status_code == 404


def test_status_transitions_and_duplicate_guard(client, env):
    from jobagent import db
    j = _make_job(db)
    assert client.post(f"/api/jobs/{j.id}/status", json={"status": "interview"}).status_code == 200
    # marking applied after interview is a duplicate application -> blocked
    r = client.post(f"/api/jobs/{j.id}/status", json={"status": "applied"})
    assert r.status_code == 409
    assert client.post(f"/api/jobs/{j.id}/status", json={"status": "nonsense"}).status_code == 400


def test_notes_endpoint(client, env):
    from jobagent import db
    j = _make_job(db)
    r = client.post(f"/api/jobs/{j.id}/notes", json={"notes": "Recruiter: Priya, follow up Friday"})
    assert r.status_code == 200
    assert "Priya" in client.get(f"/api/jobs/{j.id}").json()["notes"]


def test_apply_blocked_for_fraud_flagged(client, env):
    from jobagent import db
    from jobagent.enrich import apply_to_job, enrich
    j = _make_job(db, external_id="9", company="ScamCorp",
                  description="Pay registration fee of Rs 500. Python role.")
    facts = enrich(j.title, j.description, j.company, j.url)
    apply_to_job(j.id, facts)
    assert facts.fraud_flags
    # no master resume in test env -> 400 before the fraud check; create the file marker
    (env / "profile").mkdir(exist_ok=True)
    (env / "profile" / "master_resume.json").write_text("{}", encoding="utf-8")
    r = client.post(f"/api/jobs/{j.id}/apply")
    assert r.status_code == 409 and "trust warnings" in r.json()["detail"]


def test_upload_rejects_non_pdf(client):
    r = client.post("/api/resume/upload",
                    files={"file": ("resume.docx", BytesIO(b"PK\x03\x04 fake"), "application/docx")})
    assert r.status_code == 400


def test_upload_rejects_oversize(client, monkeypatch):
    monkeypatch.setattr("jobagent.server.MAX_UPLOAD_BYTES", 100)
    r = client.post("/api/resume/upload",
                    files={"file": ("resume.pdf", BytesIO(b"%PDF-1.4 " + b"0" * 200), "application/pdf")})
    assert r.status_code == 400


def test_upload_rejects_fake_pdf_magic(client):
    r = client.post("/api/resume/upload",
                    files={"file": ("resume.pdf", BytesIO(b"not a pdf at all"), "application/pdf")})
    assert r.status_code == 400


def test_answers_crud(client):
    assert client.put("/api/answers", json={"question": "Q", "answer": "A"}).status_code == 200
    assert client.put("/api/answers", json={"question": "", "answer": ""}).status_code == 400
    rows = client.get("/api/answers").json()
    assert len(rows) == 1
    assert client.delete(f"/api/answers/{rows[0]['id']}").status_code == 200
    assert client.get("/api/answers").json() == []


def test_notifications_flow(client):
    assert client.get("/api/notifications").json() == []
    assert client.post("/api/notifications/read-all").status_code == 200


def test_analytics_shape(client):
    body = client.get("/api/analytics").json()
    for key in ("metrics", "applications_over_time", "status_funnel", "match_distribution",
                "source_quality", "missing_skills", "eligibility", "upcoming_deadlines"):
        assert key in body


def test_ai_service_validates(client, env):
    from jobagent import db
    j = _make_job(db)
    assert client.post("/api/ai/nonsense", params={"job_id": j.id}).status_code == 404
    assert client.post("/api/ai/interview_prep", params={"job_id": "missing"}).status_code == 404


def test_search_validates_sources(client):
    r = client.post("/api/search", json={"keywords": ["AI Engineer"], "locations": ["Hyderabad"],
                                         "sources": ["google"], "max_age_days": 14,
                                         "score_limit": 0, "tailor_limit": 0})
    assert r.status_code == 400


def test_spa_path_traversal_is_safe(client):
    r = client.get("/../etc/passwd")
    assert "root:" not in r.text                     # never serves files outside dist
