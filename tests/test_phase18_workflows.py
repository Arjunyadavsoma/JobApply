"""Phase 18 — prep workflow, batch, interview prep, agents, email intelligence."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture()
def job(env, tmp_path):
    from jobagent import db
    db.upsert_jobs([db.Job(
        source="test", external_id="prep:1", company="Prep Corp", title="Python Developer",
        url="https://example.com/j/1", apply_url="https://jobs.lever.co/prep/1",
        description="Python, SQL, Django role in Hyderabad. Fresher friendly. 2027 batch welcome.",
        location="Hyderabad")])
    jid = db.job_id("test", "prep:1")
    resume_pdf = tmp_path / "resume.pdf"
    resume_pdf.write_bytes(b"%PDF-1.4 " + b"y" * 20000)
    (tmp_path / "resume.json").write_text(json.dumps({
        "resume": {"summary": "Python SQL Django developer", "skills": [],
                   "experience": [], "projects": []}}), encoding="utf-8")
    db.update(jid, score=85, prefit=80, verification_state="verified_open",
              eligibility_json='{"status": "eligible"}',
              match_json='{"missing_skills": [], "missing_requirements": []}',
              resume_path=str(resume_pdf), quality_score=70)
    return db.get(jid)


# ------------------------------------------------------------------ prep workflow

def test_prepare_runs_all_steps_offline(job):
    """skip_llm mode: deterministic steps run, LLM steps degrade honestly."""
    from jobagent import prep
    out = prep.prepare(job["id"], skip_llm=True)
    names = [s["step"] for s in out["steps"]]
    assert names == ["analyze_jd", "verify_eligibility", "match_score", "missing_skills",
                     "select_resume", "tailor_resume", "cover_letter", "prepare_answers",
                     "validate_facts", "company_research", "readiness", "checkpoint",
                     "request_approval"]
    by = {s["step"]: s for s in out["steps"]}
    assert by["analyze_jd"]["ran"] is False and "offline" in by["analyze_jd"]["detail"]
    assert by["verify_eligibility"]["ran"] is True
    assert by["match_score"]["data"] == 85
    assert by["readiness"]["ran"] is True
    assert by["checkpoint"]["ran"] is True
    assert out["ready"] is True
    # queue advanced honestly
    from jobagent import db
    assert db.get(job["id"])["queue_state"] == "ready"
    # preview carries everything a human reviews before submitting
    pv = out["preview"]
    assert pv["job"]["company"] == "Prep Corp"
    assert pv["resume_path"]
    assert pv["approval_required"] is True


def test_prepare_with_llm_agents(job, monkeypatch):
    from jobagent import prep

    # stub job_research agent
    import jobagent.agents as agents

    def fake_job_research(job_id):
        return {"red_flags": ["long hours"], "top_requirements": ["Python"]}

    monkeypatch.setattr(agents, "run", lambda a, j: (
        fake_job_research(j) if a == "job_research" else (_ for _ in ()).throw(
            AssertionError(f"unexpected agent {a}"))))

    # company_intel offline-safe: make it fail so the step degrades gracefully
    import jobagent.company_intel as ci
    def boom_research(job_id, force=False):
        raise RuntimeError("offline")
    monkeypatch.setattr(ci, "research", boom_research)

    out = prep.prepare(job["id"])
    by = {s["step"]: s for s in out["steps"]}
    assert by["analyze_jd"]["ran"] is True
    assert by["company_research"]["ran"] is False
    assert "unavailable" in by["company_research"]["detail"]
    assert out["ready"] is True


# ------------------------------------------------------------------ batch workflow

def _mk_job(env, key, company, score=85, **kw):
    from jobagent import db
    db.upsert_jobs([db.Job(
        source="test", external_id=f"batch:{key}", company=company, title="SDE",
        url=f"https://example.com/{key}", apply_url=f"https://jobs.lever.co/{key}/1",
        description="Python role", location="Hyderabad")])
    jid = db.job_id("test", f"batch:{key}")
    db.update(jid, score=score, prefit=score, verification_state="verified_open",
              eligibility_json='{"status": "eligible"}', quality_score=70, **kw)
    return jid


def test_batch_prepare_with_controls(env, job, monkeypatch):
    from jobagent import batch
    from jobagent import prep as prep_module

    # deterministic prep (no LLM)
    monkeypatch.setattr(prep_module, "prepare",
                        lambda jid, skip_llm=False: {"ready": True,
                                                     "readiness": {"overall": 88}})

    keep = job["id"]
    ok2 = _mk_job(env, 2, "Good Corp")
    excluded = _mk_job(env, 3, "Bad Corp")
    low_score = _mk_job(env, 4, "Low Corp", score=40)
    wrong_loc = _mk_job(env, 5, "Far Corp")
    from jobagent import db
    db.update(wrong_loc, location="Remote-US")

    out = batch.batch_prepare(
        [keep, ok2, excluded, low_score, wrong_loc],
        controls={"excluded_companies": ["bad corp"], "allowed_locations": ["Hyderabad"]})

    assert out["requested"] == 5
    assert out["prepared"] == 2
    assert out["skipped"] == 3
    by_company = {r.get("company"): r for r in out["results"]}
    assert by_company["Bad Corp"]["skipped"] == "company is excluded"
    assert "match score 40" in by_company["Low Corp"]["skipped"]
    assert "location not allowed" in by_company["Far Corp"]["skipped"]
    assert by_company["Prep Corp"]["ok"] is True
    # audit trail
    runs = batch.batch_history()
    assert runs and runs[0]["result"]["prepared"] == 2


def test_batch_never_submits_and_blocks_duplicates(env, job, monkeypatch):
    from jobagent import batch
    from jobagent import db
    from jobagent import prep as prep_module
    monkeypatch.setattr(prep_module, "prepare",
                        lambda jid, skip_llm=False: {"ready": True,
                                                     "readiness": {"overall": 90}})
    # simulate an existing submission for this job
    db.record_submission(job["id"], "manual", {"note": "already sent"}, confirmed=True)
    out = batch.batch_prepare([job["id"]])
    assert out["prepared"] == 0
    assert "duplicate protection" in out["results"][0].get("skipped", "")
    # nothing new was submitted by the batch itself
    assert len(db.submissions_for(job["id"])) == 1


def test_batch_size_cap(env, monkeypatch):
    from jobagent import batch
    from jobagent import prep as prep_module
    monkeypatch.setattr(prep_module, "prepare",
                        lambda jid, skip_llm=False: {"ready": True,
                                                     "readiness": {"overall": 80}})
    ids = [_mk_job(env, f"c{i}", f"Corp {i}") for i in range(8)]
    out = batch.batch_prepare(ids, controls={"max_batch_size": 3})
    assert out["requested"] == 3          # capped


# ------------------------------------------------------------------ interview prep

def test_interview_prep_plan_and_progress(job, monkeypatch):
    from jobagent import interview_prep as ip
    from jobagent.company_intel import CompanyIntel
    from jobagent.interview_prep import PrepQuestion

    def fake_structured(schema, system=None, content=None, effort="high", task="", **kw):
        assert schema.__name__ == "InterviewPrepPlan"
        assert "UNTRUSTED" in system or "untrusted" in system
        questions = []
        for i, cat in enumerate(("company", "technical", "python", "sql", "dsa",
                                 "ai_ml", "project", "hr", "behavioral")):
            questions.append(PrepQuestion(
                id=f"q_{cat}", category=cat,
                question=f"Q about {cat}?",
                explanation="probes understanding",
                model_answer="Grounded answer from my resume.",
                difficulty="medium"))
        return schema(company="Prep Corp", role="Python Developer",
                      focus_areas=["Python", "SQL"], questions=questions,
                      talking_points=["Resume projects"], generated_at="")

    import jobagent.llm as llm
    monkeypatch.setattr(llm, "structured", fake_structured)

    plan = ip.plan(job["id"])
    assert len(plan["questions"]) == 9
    cats = {q["category"] for q in plan["questions"]}
    assert cats == {"company", "technical", "python", "sql", "dsa", "ai_ml",
                    "project", "hr", "behavioral"}
    # cached
    plan2 = ip.plan(job["id"])
    assert plan2["questions"] == plan["questions"]

    # mock session flow
    q = ip.mock_question(job["id"], category="python")
    assert q["question"]["id"] == "q_python"
    assert q["reveal_answer"] is False
    ans = ip.mock_answer(job["id"], "q_python", answered=True)
    assert "Grounded answer" in ans["model_answer"]
    assert ans["progress"]["attempted"]["python"]["answered"] == 1
    # progress summary shows per-category counts
    prog = ip.progress(job["id"])
    assert prog["total_questions"] == 9
    assert prog["per_category"]["python"] == 1


def test_interview_prep_degrades_without_llm(job, monkeypatch):
    import jobagent.llm as llm
    from jobagent import interview_prep as ip

    def boom(*a, **kw):
        raise RuntimeError("groq down")
    monkeypatch.setattr(llm, "structured", boom)
    with pytest.raises(RuntimeError):
        ip.plan(job["id"])
    assert ip.mock_question(job["id"]) is None      # nothing fake served


# ------------------------------------------------------------------ agents

def test_agent_registry_and_history(job, monkeypatch):
    import jobagent.agents as agents

    reg = {a["key"] for a in agents.registry()}
    assert {"job_research", "company_research", "job_matching", "resume_tailoring",
            "cover_letter", "application_answer", "application_quality",
            "form_understanding", "interview_prep", "follow_up"} == reg

    # execution history records success + failure
    from jobagent.llm import LLMError

    class _R:
        company_snapshot = "x"
        role_summary = "y"
        top_requirements = []
        red_flags = []
        questions_to_ask = []

        def model_dump(self):
            return {"company_snapshot": self.company_snapshot}

    def ok_structured(*a, **kw):
        return _R()

    import jobagent.llm as llm
    monkeypatch.setattr(agents, "structured", ok_structured)
    agents.run("job_research", job["id"])

    def boom(*a, **kw):
        raise LLMError("rate limited")

    monkeypatch.setattr(agents, "structured", boom)
    with pytest.raises(LLMError):
        agents.run("job_research", job["id"])

    hist = agents.history(agent="job_research")
    assert len(hist) == 2
    assert hist[0]["ok"] == 0 and "rate limited" in hist[0]["error"]
    assert hist[1]["ok"] == 1


def test_shared_candidate_context(job, monkeypatch):
    import jobagent.agents as agents
    # give the profile something to say
    from jobagent.models import CandidateProfile
    from jobagent import profile as profile_mod
    monkeypatch.setattr(profile_mod, "load",
                        lambda: CandidateProfile(full_name="Arjun", email="a@x.com",
                                                 graduation_year=2027))
    ctx = agents.shared_candidate_context(job["id"])
    assert "<candidate_context>" in ctx
    assert "2027" in ctx
    assert "<verified_profile_facts>" in ctx


def test_form_understanding_agent_dispatch(job):
    import jobagent.agents as agents
    out = agents.run("form_understanding", job["id"])
    assert "classified" in out and out["fields_seen"] == 0   # no checkpoint fields yet


# ------------------------------------------------------------------ email intelligence

def test_email_refinements():
    from jobagent.email_sync import refine
    assert refine("interview", "Interview", "Your technical round is scheduled") == "technical_interview"
    assert refine("interview", "Interview", "HR round details") == "hr_interview"
    assert refine("assessment", "Test", "Please complete the online test on HackerRank") == "online_test"
    assert refine("other", "BGV", "Please submit documents for background verification") == "background_verification"
    assert refine("offer", "Offer", "We are pleased to offer") == "offer"      # unchanged
    assert refine("confirmation", "Thanks", "we have received your application") == "confirmation"


def test_email_entity_extraction():
    from jobagent.email_sync import extract_entities
    e = extract_entities(
        "Interview invitation - Python Developer role at Prep Corp",
        "Hi Arjun,\n\nYour technical interview is scheduled for 15 March 2027 at 10:30 AM IST.\n"
        "Join here: https://meet.google.com/abc-defg-hij\n"
        "Please complete the pre-interview form by 12 March.\n\n"
        "Regards,\nPriya Sharma\nTalent Acquisition Team",
        sender="Priya Sharma <priya.sharma@prepcorp.com>")
    assert "15 March" in e["date"]
    assert "10:30 AM" in e["time"].upper() or "10:30" in e["time"]
    assert "12 March" in e["deadline"]
    assert "meet.google.com" in e["meeting_link"]
    assert e["recruiter_name"] == "Priya Sharma"
    assert e["recruiter_email"] == "priya.sharma@prepcorp.com"
    assert "Python Developer" in e["role"]


def test_email_entities_stored_with_message(job):
    from jobagent import db
    from jobagent.email_sync import process_messages
    # emails only match applications that are already in the pipeline
    db.update(job["id"], status="applied")
    stats = process_messages([{
        "message_id": "<e1@x>", "sender": "Priya <p@corp.com>",
        "subject": "Technical interview - Prep Corp",
        "body": "Your technical round is on 20 March at 2 PM IST. Join: https://zoom.us/j/123",
        "received_at": "2026-01-01T00:00:00+00:00"}])
    assert stats["new"] == 1
    rows = db.emails()
    row = next(r for r in rows if r["message_id"] == "<e1@x>")
    assert row["classification"] == "technical_interview"
    entities = json.loads(row["entities"])
    assert "zoom.us" in entities["meeting_link"]
    assert entities["recruiter_email"] == "p@corp.com"
    # the matched application moved to interview
    assert db.get(job["id"])["status"] == "interview"
