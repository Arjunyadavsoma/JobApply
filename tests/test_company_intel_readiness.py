"""Phase 18 — company intelligence + readiness score tests (no network, stubbed LLM)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture()
def job(env):
    from jobagent import db
    db.upsert_jobs([db.Job(
        source="test", external_id="intel:1", company="Sandbox AI Labs",
        title="ML Engineer", url="https://example.com/jobs/1",
        apply_url="https://boards.greenhouse.io/sandbox/1",
        description="We build ML tooling. Python, PyTorch, SQL required. "
                    "Office in Hyderabad. Interview process: 2 rounds.")])
    return db.get(db.job_id("test", "intel:1"))


# ------------------------------------------------------------------ company intel

def test_research_tags_verified_vs_interpretation(job, monkeypatch):
    from jobagent import company_intel as ci
    from jobagent.company_intel import CompanyIntel

    def fake_structured(schema, system=None, content=None, effort="medium", task="", **kw):
        assert schema is CompanyIntel
        return CompanyIntel(
            overview="Builds ML tooling for enterprise teams.",
            products=["Sandbox ML Studio"],
            tech_stack=["Python", "PyTorch"],
            role_responsibilities=["Train models", "Write pipelines"],
            required_qualifications=["B.Tech", "Python"],
            locations=["Hyderabad"],
            recruitment_process="2 rounds described in the posting",
            facts_verified=["posting says: Python, PyTorch, SQL required",
                            "posting says: office in Hyderabad"],
            ai_interpretations=["likely a product company based on the tooling focus"],
        )

    import jobagent.llm as llm
    monkeypatch.setattr(llm, "structured", fake_structured)

    out = ci.research(job["id"])
    intel = out["intel"]
    assert intel["overview"].startswith("Builds ML tooling")
    assert len(intel["facts_verified"]) == 2
    assert len(intel["ai_interpretations"]) == 1
    assert "https://example.com/jobs/1" in out["sources"]   # the posting itself is cited
    # cached now
    cached = ci.get(job["id"])
    assert cached and cached["intel"]["company"] == "Sandbox AI Labs"
    # a second research call serves from cache without calling the LLM again
    calls = {"n": 0}

    def boom(*a, **kw):
        calls["n"] += 1
        raise AssertionError("cache should have prevented an LLM call")

    monkeypatch.setattr(llm, "structured", boom)
    out2 = ci.research(job["id"])
    assert out2["intel"]["company"] == "Sandbox AI Labs"
    assert calls["n"] == 0


def test_research_context_separates_facts_from_interpretation(job, monkeypatch):
    from jobagent import company_intel as ci
    from jobagent.company_intel import CompanyIntel

    def fake_structured(schema, **kw):
        return CompanyIntel(overview="ML tooling company.", tech_stack=["Python"],
                            facts_verified=["posting says: Python required"],
                            ai_interpretations=["probably product-led"])

    import jobagent.llm as llm
    monkeypatch.setattr(llm, "structured", fake_structured)
    ci.research(job["id"])
    ctx = ci.answer_context(job["id"])
    assert "Verified: posting says: Python required" in ctx
    assert "(interpretation, not verified): probably product-led" in ctx


def test_no_fake_research_when_llm_fails(job, monkeypatch):
    from jobagent import company_intel as ci

    def boom(*a, **kw):
        raise RuntimeError("groq down")

    import jobagent.llm as llm
    monkeypatch.setattr(llm, "structured", boom)
    with pytest.raises(RuntimeError):
        ci.research(job["id"])
    assert ci.get(job["id"]) is None        # nothing cached, nothing invented


# ------------------------------------------------------------------ readiness

def _prep_job(env, tmp_path, **kw):
    from jobagent import db
    db.upsert_jobs([db.Job(
        source="test", external_id="ready:1", company="Ready Corp",
        title="Python Developer", url="https://example.com/j/1",
        apply_url="https://jobs.lever.co/ready/1",
        description="Python developer role: Python, SQL, REST APIs, Django, "
                    "testing, cloud, Hyderabad office, fresher friendly.")])
    jid = db.job_id("test", "ready:1")
    resume_pdf = tmp_path / "resume.pdf"
    resume_pdf.write_bytes(b"%PDF-1.4 " + b"x" * 20000)
    cover = tmp_path / "cover.txt"
    cover.write_text("Dear team, ...")
    (tmp_path / "resume.json").write_text(json.dumps({
        "resume": {"summary": "Python SQL Django REST developer",
                   "skills": [{"category": "langs", "items": ["Python", "SQL"]}],
                   "experience": [], "projects": []}}), encoding="utf-8")
    defaults = dict(score=82, prefit=80, verification_state="verified_open",
                    eligibility_json='{"status": "eligible"}',
                    match_json='{"missing_skills": [], "missing_requirements": []}',
                    resume_path=str(resume_pdf), cover_path=str(cover))
    defaults.update(kw)
    db.update(jid, **defaults)
    return jid


def test_readiness_full_score(env, tmp_path):
    from jobagent.readiness import score
    jid = _prep_job(env, tmp_path)
    r = score(jid)
    assert r["overall"] >= 85, r
    assert r["submission_ready"] is True
    assert r["ready_for_authorized_submission"] is True
    assert r["blocking_issues"] == []
    assert 90 <= r["dimensions"]["candidate_eligibility"] <= 100
    assert r["dimensions"]["url_validity"] == 100


def test_readiness_blocks_on_missing_resume_and_bad_url(env, tmp_path):
    from jobagent.readiness import score
    jid = _prep_job(env, tmp_path, resume_path="", verification_state="expired")
    r = score(jid)
    assert r["submission_ready"] is False
    assert any("no tailored resume" in b for b in r["blocking_issues"])
    assert any("expired" in b for b in r["blocking_issues"])
    assert r["dimensions"]["resume_formatting"] == 0


def test_readiness_flags_low_match_and_missing_skills(env, tmp_path):
    from jobagent.readiness import score
    jid = _prep_job(env, tmp_path, score=45,
                    match_json='{"missing_skills": ["Kubernetes", "Spark"]}')
    r = score(jid)
    assert r["submission_ready"] is False
    assert any("match score 45" in b for b in r["blocking_issues"])
    assert any("Kubernetes" in i for i in r["improvements"])
    assert r["dimensions"]["resume_relevance"] == 45


def test_readiness_unknown_fields_never_perfect(env, tmp_path):
    """Unknown dimensions must cost points, not default to 100."""
    from jobagent.readiness import score
    jid = _prep_job(env, tmp_path, eligibility_json="", match_json="",
                    verification_state="unverified")
    r = score(jid)
    assert r["overall"] < 85
    assert any("not evaluated" in w for w in r["warnings"])
    assert any("not verified" in w for w in r["warnings"])
    assert r["submission_ready"] is False


def test_readiness_keyword_alignment(env, tmp_path):
    from jobagent.readiness import score
    jid = _prep_job(env, tmp_path)
    good = score(jid)
    # rewrite the resume JSON with zero JD keywords
    (tmp_path / "resume.json").write_text(json.dumps({
        "resume": {"summary": "chef baker pastry", "skills": [], "experience": [],
                   "projects": []}}), encoding="utf-8")
    bad = score(jid)
    assert bad["dimensions"]["keyword_alignment"] < good["dimensions"]["keyword_alignment"]
    assert bad["dimensions"]["keyword_alignment"] < 60
