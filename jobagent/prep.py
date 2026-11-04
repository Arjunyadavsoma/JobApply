"""Phase 18.4 — the application preparation workflow.

One orchestrated pipeline per shortlisted job, running the steps the spec lists:

  1. analyze the JD (Job Research Agent)
  2. verify eligibility (deterministic engine)
  3. calculate the match score (Job Matching Agent)
  4. identify missing skills (from the matcher breakdown)
  5. select the best resume version (master vs existing tailored)
  6. tailor the resume (Resume Tailoring Agent)
  7. generate a cover letter when useful (Cover Letter Agent)
  8. prepare application answers (saved KB + Application Answer Agent)
  9. validate factual accuracy (profile vs resume consistency)
 10. company research (Company Research Agent, cached)
 11. readiness score (readiness.py - the go/no-go signal)
 12. save the application checkpoint (queue state + preview payload)
 13. request final approval where required (mode C gate is untouched)

Every step records what ACTUALLY ran; nothing is assumed. The function returns
a structured preview the dashboard shows before anything is submitted. Steps
that need the LLM degrade gracefully (marked skipped) when Groq is unavailable.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jobagent import db


def _step(name: str, ran: bool, detail: str = "", data: Any = None) -> dict[str, Any]:
    return {"step": name, "ran": ran, "detail": detail, "data": data}


def prepare(job_id: str, *, skip_llm: bool = False) -> dict[str, Any]:
    """Run the full preparation pipeline for one job. Never submits anything."""
    from jobagent.queue import set_queue_state
    job = db.get(job_id)
    if not job:
        raise KeyError(job_id)
    steps: list[dict[str, Any]] = []
    set_queue_state(job_id, "resume_prep", "preparation workflow started")

    # 1. JD analysis (Job Research Agent) - cached event-wise, safe to skip offline
    if skip_llm:
        steps.append(_step("analyze_jd", False, "skipped (offline mode)"))
    else:
        try:
            from jobagent.agents import run as agent_run
            briefing = agent_run("job_research", job_id)
            steps.append(_step("analyze_jd", True,
                               "role briefing + red flags generated",
                               {"red_flags": briefing.get("red_flags", [])[:5],
                                "top_requirements": briefing.get("top_requirements", [])[:5]}))
        except Exception as e:                               # noqa: BLE001
            steps.append(_step("analyze_jd", False, f"LLM unavailable: {str(e)[:120]}"))

    # 2. eligibility (deterministic)
    try:
        elig = json.loads(job["eligibility_json"] or "{}").get("status", "")
    except ValueError:
        elig = ""
    if not elig:
        from jobagent.eligibility import evaluate, row_to_facts
        from jobagent.profile import load as load_profile
        result = evaluate(row_to_facts(job), load_profile())
        db.update(job_id, eligibility_json=result.model_dump_json())
        elig = result.status
    steps.append(_step("verify_eligibility", True, f"eligibility: {elig}", elig))

    # 3. match score
    score_v = job["score"] or job["prefit"] or 0
    steps.append(_step("match_score", True, f"match score: {score_v}", score_v))

    # 4. missing skills
    try:
        match = json.loads(job["match_json"] or "{}")
    except ValueError:
        match = {}
    missing = match.get("missing_skills") or []
    steps.append(_step("missing_skills", True,
                       f"{len(missing)} missing skill(s)", missing[:8]))

    # 5. resume selection: existing tailored resume, else the master
    if job["resume_path"] and Path(job["resume_path"]).exists():
        steps.append(_step("select_resume", True,
                           "existing tailored resume reused", job["resume_path"]))
        tailor_needed = False
    else:
        steps.append(_step("select_resume", True, "master resume selected for tailoring"))
        tailor_needed = True

    # 6 + 7. tailor resume + cover letter (one agent run produces both)
    if tailor_needed and not skip_llm:
        try:
            from jobagent.agents import run as agent_run
            out = agent_run("resume_tailoring", job_id)
            steps.append(_step("tailor_resume", True, "tailored resume generated",
                               out.get("resume_path")))
            job = db.get(job_id)
            if job["cover_path"] and Path(job["cover_path"]).exists():
                steps.append(_step("cover_letter", True, "cover letter generated",
                                   job["cover_path"]))
            else:
                steps.append(_step("cover_letter", False, "no cover letter (not required)"))
        except Exception as e:                               # noqa: BLE001
            steps.append(_step("tailor_resume", False, f"tailoring failed: {str(e)[:120]}"))
            steps.append(_step("cover_letter", False, "skipped (tailoring failed)"))
    else:
        steps.append(_step("tailor_resume", not tailor_needed,
                           "skipped" if not tailor_needed else "skipped (offline mode)"))
        cover_ok = bool(job["cover_path"]) and Path(job["cover_path"]).exists()
        steps.append(_step("cover_letter", cover_ok,
                           "existing cover letter" if cover_ok else "no cover letter"))

    # 8. application answers: KB hits for likely screening questions
    answers = [{"question": a["question"], "answer": a["answer"][:200]}
               for a in db.list_answers()[:10]]
    steps.append(_step("prepare_answers", True,
                       f"{len(answers)} saved answer(s) available", answers))

    # 9. factual accuracy validation (readiness dimension 8)
    from jobagent.readiness import score as readiness_score
    r = readiness_score(job_id)
    factual = r["dimensions"].get("factual_consistency", 0)
    steps.append(_step("validate_facts", True,
                       f"factual consistency: {factual}/100",
                       r.get("warnings", [])))

    # 10. company research (cached, degrades when offline)
    if not skip_llm:
        try:
            from jobagent import company_intel
            out = company_intel.research(job_id)
            intel = out["intel"]
            steps.append(_step("company_research", True,
                               f"{len(intel.get('facts_verified', []))} verified fact(s)",
                               {"overview": intel.get("overview", "")[:300],
                                "sources": out["sources"]}))
        except Exception as e:                               # noqa: BLE001
            steps.append(_step("company_research", False,
                               f"unavailable: {str(e)[:120]}"))
    else:
        steps.append(_step("company_research", False, "skipped (offline mode)"))

    # 11. readiness score (the go/no-go signal)
    steps.append(_step("readiness", True, f"readiness {r['overall']}/100", r))

    # 12. checkpoint: advance the queue honestly based on what is actually ready
    job = db.get(job_id)
    ready = bool(job["resume_path"]) and Path(job["resume_path"]).exists()
    db.update(job_id, queue_ready=1 if ready else 0)
    set_queue_state(job_id, "ready" if ready else "needs_manual",
                    "preparation workflow finished" if ready else "resume not ready")
    steps.append(_step("checkpoint", True,
                       "queue state: " + ("ready" if ready else "needs_manual")))

    # 13. approval request note (the gate itself stays in execution.mode_c)
    steps.append(_step("request_approval", True,
                       "approval is required before any authorized submission "
                       "(queue action 'approve' + automation.authorized_submission)"))

    db.record_event(job_id, "queue", "Preparation workflow completed")
    return {
        "job_id": job_id,
        "company": job["company"],
        "title": job["title"],
        "steps": steps,
        "readiness": r,
        "ready": ready,
        "preview": preview(job_id),
    }


def preview(job_id: str) -> dict[str, Any]:
    """The complete application preview shown before submission."""
    job = db.get(job_id)
    if not job:
        raise KeyError(job_id)
    cover = ""
    if job["cover_path"] and Path(job["cover_path"]).exists():
        cover = Path(job["cover_path"]).read_text(encoding="utf-8")[:1500]
    resume = None
    if job["resume_path"] and Path(job["resume_path"]).exists():
        rj = Path(job["resume_path"]).parent / "resume.json"
        try:
            resume = json.loads(rj.read_text(encoding="utf-8")) if rj.exists() else None
        except ValueError:
            resume = None
    try:
        verification = {"state": job["verification_state"],
                        "final_url": job["final_url"] or job["apply_url"]}
    except Exception:                                        # noqa: BLE001
        verification = {"state": "unknown"}
    return {
        "job": {"company": job["company"], "title": job["title"],
                "location": job["location"], "salary": job["salary_text"],
                "apply_url": job["apply_url"], "match_score": job["score"] or job["prefit"]},
        "resume_path": job["resume_path"],
        "resume": resume,
        "cover_letter": cover,
        "saved_answers": [{"question": a["question"], "answer": a["answer"]}
                          for a in db.list_answers()[:10]],
        "verification": verification,
        "queue_state": job["queue_state"],
        "apply_mode": job["apply_mode"],
        "approval_required": True,
    }
