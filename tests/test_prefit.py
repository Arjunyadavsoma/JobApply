"""Prefit (local fit estimate) tests - especially the profile-aware level penalty."""
from __future__ import annotations

from jobagent.prefit import _wrong_levels, estimate, resume_skills
from jobagent.models import Resume, SkillGroup, Project

SKILLS = ["python", "machine learning", "sql", "pandas", "pytorch", "docker", "aws", "git"]


def test_fresher_titles_not_penalised_for_fresher():
    """THE bug fix: fresher/trainee/intern titles are the target for a fresher candidate."""
    assert "fresher" not in _wrong_levels("fresher")
    assert "intern" not in _wrong_levels("fresher")
    assert "senior" in _wrong_levels("fresher")          # seniors ARE penalised instead


def test_senior_titles_penalised_for_experienced():
    assert "intern" in _wrong_levels("experienced")
    assert "fresher" in _wrong_levels("experienced")
    assert "senior" not in _wrong_levels("experienced")


def _job_row(title, description=""):
    class R(dict):
        __getattr__ = dict.get
    return R({"title": title, "description": description, "prefit": None})


def test_estimate_scores_good_fresher_fit_higher_than_wrong_stack():
    good = estimate(_job_row("AI Engineer Intern", "python, pytorch, machine learning, docker"),
                    SKILLS, ["AI Engineer"], 0)
    bad = estimate(_job_row("Senior SAP ABAP Architect", "sap, abap, 10 years"),
                   SKILLS, ["AI Engineer"], 0)
    assert good > bad


def test_fresher_internship_beats_senior_role_for_fresher_candidate():
    intern = estimate(_job_row("ML Intern", "python pytorch"), SKILLS, ["ML Engineer"], 0)
    senior = estimate(_job_row("Staff ML Engineer", "python pytorch, lead a team"),
                      SKILLS, ["ML Engineer"], 0)
    assert intern > senior            # before the fix both were penalised equally


def test_resume_skills_extraction():
    r = Resume(
        name="Test", email="t@x.io", phone="", location="", links=[], summary=None,
        skills=[SkillGroup(category="Languages", items=["Python", "SQL"]),
                SkillGroup(category="Core Competencies", items=["Teamwork"])],
        experience=[], projects=[Project(name="P", tech=["PyTorch", "Docker"], url=None, bullets=[])],
        education=[], certifications=[], achievements=[],
    )
    skills = resume_skills(r)
    assert "python" in skills and "pytorch" in skills
    assert "teamwork" not in skills                   # soft competencies excluded
