"""Deterministic eligibility engine tests: the 4-level classification rules."""
from __future__ import annotations

from jobagent.eligibility import evaluate
from jobagent.models import CandidateProfile


def profile(**kw) -> CandidateProfile:
    base = dict(graduation_year=2027, cgpa=7.8, tenth_percentage=85, twelfth_percentage=92,
                active_backlogs=0, previous_backlogs=0, candidate_type="fresher",
                total_experience_years=0, preferred_locations=["Hyderabad"], open_to_relocation=True)
    base.update(kw)
    return CandidateProfile(**base)


def test_batch_eligible():
    r = evaluate({"batch_years": [2026, 2027, 2028]}, profile())
    assert r.status == "eligible"
    assert r.checks[0].result == "pass"


def test_batch_not_eligible():
    r = evaluate({"batch_years": [2024, 2025]}, profile())
    assert r.status == "not_eligible"


def test_batch_unknown_needs_verification():
    r = evaluate({"batch_years": [2026, 2027]}, profile(graduation_year=None))
    assert r.status == "needs_verification"
    assert all(c.result == "unknown" for c in r.checks)


def test_cgpa_pass_and_fail():
    assert evaluate({"min_cgpa": 6.0}, profile()).status == "eligible"
    assert evaluate({"min_cgpa": 8.0}, profile(cgpa=7.0)).status == "not_eligible"


def test_cgpa_missing_profile_is_never_rejected():
    r = evaluate({"min_cgpa": 6.0}, profile(cgpa=None))
    assert r.status == "needs_verification"


def test_percentage_checks_lowest_marks():
    assert evaluate({"min_percentage": 80}, profile()).status == "eligible"
    assert evaluate({"min_percentage": 90}, profile()).status == "not_eligible"   # 85 < 90
    assert evaluate({"min_percentage": 60}, profile(tenth_percentage=None,
                                                    twelfth_percentage=None)).status == "needs_verification"


def test_experience_borderline_is_unknown_not_fail():
    assert evaluate({"experience_min": 0.0}, profile()).status == "eligible"
    r = evaluate({"experience_min": 1.0}, profile(total_experience_years=0.5))
    assert r.status == "needs_verification"          # within a year -> verify, don't reject
    assert evaluate({"experience_min": 5.0}, profile()).status == "not_eligible"


def test_backlogs():
    assert evaluate({"backlog_policy": "no_active_backlogs"}, profile()).status == "eligible"
    assert evaluate({"backlog_policy": "no_active_backlogs"},
                    profile(active_backlogs=2)).status == "not_eligible"
    # cleared backlogs are fine for 'no ACTIVE backlogs' but fail 'no backlogs ever'
    assert evaluate({"backlog_policy": "no_active_backlogs"},
                    profile(previous_backlogs=1)).status == "eligible"
    assert evaluate({"backlog_policy": "no_backlogs"},
                    profile(previous_backlogs=1)).status == "not_eligible"


def test_fraud_flag_blocks_eligibility():
    r = evaluate({"fraud_flags": ["asks for a registration fee"]}, profile())
    assert r.status == "not_eligible"
    assert any(c.criterion == "trust" for c in r.checks)


def test_no_constraints_is_eligible():
    assert evaluate({}, profile()).status == "eligible"


def test_never_auto_rejects_on_unclear_criteria():
    """A posting with only vague requirements must degrade to needs_verification, never fail."""
    r = evaluate({"min_cgpa": 7.0, "batch_years": [2026, 2027]},
                 profile(cgpa=None, graduation_year=None))
    assert r.status == "needs_verification"
    assert all(c.result == "unknown" for c in r.checks)
