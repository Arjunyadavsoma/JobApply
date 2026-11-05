"""Deterministic fresher eligibility engine (India-first).

Compares enriched job facts against the candidate profile and classifies:
  eligible | likely_eligible | needs_verification | not_eligible

Rules of the game (deliberate product decisions):
- Explicit employer requirements are checked deterministically; the LLM NEVER overrides them.
- Unclear criteria never auto-reject: anything unreadable becomes `unknown` and the overall
  status degrades to needs_verification at worst.
- The candidate profile is editable data, never hardcoded rules.
"""
from __future__ import annotations

from jobagent.models import CandidateProfile, EligibilityCheck, EligibilityResult


def _check(criterion: str, required: str, candidate: str, result: str, note: str = "") -> EligibilityCheck:
    return EligibilityCheck(criterion=criterion, required=required, candidate=candidate,
                            result=result, note=note)  # type: ignore[arg-type]


def _pct_from_cgpa(cgpa: float, scale: float) -> float | None:
    """Rough CGPA->% conversion used only for display, never for pass/fail."""
    return round(cgpa * 10.0 * (10.0 / scale), 1) if scale else None


def evaluate(facts: dict, profile: CandidateProfile) -> EligibilityResult:
    """`facts` is a dict with the enriched job columns (sqlite Row works too)."""
    checks: list[EligibilityCheck] = []
    fails = 0
    unknowns = 0

    # ---- graduation year / batch (the big one for Indian fresher drives) ----
    batch_years = facts.get("batch_years") or []
    if isinstance(batch_years, str):
        import json
        try:
            batch_years = json.loads(batch_years) if batch_years.strip() else []
        except ValueError:
            batch_years = []
    grad_year = profile.graduation_year
    if batch_years:
        if grad_year is None:
            checks.append(_check("graduation_year", f"batch {batch_years}", "unknown", "unknown",
                                 "Set your graduation year in the profile to auto-check batches"))
            unknowns += 1
        elif grad_year in batch_years:
            checks.append(_check("graduation_year", f"batch {batch_years}", str(grad_year), "pass"))
        else:
            checks.append(_check("graduation_year", f"batch {batch_years}", str(grad_year), "fail",
                                 "Your graduation year is not in the drive's eligible batches"))
            fails += 1

    # ---- experience ----
    exp_min, exp_max = facts.get("experience_min"), facts.get("experience_max")
    if exp_min is not None:
        years = profile.total_experience_years or 0.0
        if years >= float(exp_min):
            checks.append(_check("experience", f"min {exp_min:g} yrs", f"{years:g} yrs", "pass"))
        elif years + 1.0 >= float(exp_min):     # borderline: applications often flex by ~1 year
            checks.append(_check("experience", f"min {exp_min:g} yrs", f"{years:g} yrs", "unknown",
                                 "Slightly under the stated experience bar"))
            unknowns += 1
        else:
            checks.append(_check("experience", f"min {exp_min:g} yrs", f"{years:g} yrs", "fail"))
            fails += 1

    # ---- CGPA cutoff ----
    min_cgpa = facts.get("min_cgpa")
    if min_cgpa is not None:
        if profile.cgpa is None:
            checks.append(_check("cgpa", f"min {min_cgpa:g} CGPA", "unknown", "unknown",
                                 "Add your CGPA to the profile"))
            unknowns += 1
        elif profile.cgpa >= float(min_cgpa):
            checks.append(_check("cgpa", f"min {min_cgpa:g} CGPA", f"{profile.cgpa:g}", "pass"))
        else:
            checks.append(_check("cgpa", f"min {min_cgpa:g} CGPA", f"{profile.cgpa:g}", "fail"))
            fails += 1

    # ---- percentage cutoff (applies to any of 10th / 12th / degree) ----
    min_pct = facts.get("min_percentage")
    if min_pct is not None:
        marks = [m for m in (profile.tenth_percentage, profile.twelfth_percentage,
                             profile.diploma_percentage) if m is not None]
        if marks:
            if min(marks) >= float(min_pct):
                checks.append(_check("percentage", f"min {min_pct:g}% in 10th/12th",
                                     f"10th {profile.tenth_percentage or '-'}%,"
                                     f" 12th {profile.twelfth_percentage or '-'}%", "pass"))
            else:
                checks.append(_check("percentage", f"min {min_pct:g}% in 10th/12th",
                                     f"lowest {min(marks):g}%", "fail"))
                fails += 1
        else:
            checks.append(_check("percentage", f"min {min_pct:g}%", "unknown", "unknown",
                                 "Add your 10th/12th marks to the profile"))
            unknowns += 1

    # ---- backlogs ----
    policy = facts.get("backlog_policy") or ""
    if policy == "no_backlogs" and (profile.active_backlogs or profile.previous_backlogs):
        checks.append(_check("backlogs", "no backlogs ever",
                             f"{profile.active_backlogs} active, {profile.previous_backlogs} cleared", "fail"))
        fails += 1
    elif policy == "no_active_backlogs" and profile.active_backlogs:
        checks.append(_check("backlogs", "no active backlogs",
                             f"{profile.active_backlogs} active", "fail"))
        fails += 1
    elif policy:
        checks.append(_check("backlogs", policy.replace("_", " "),
                             f"{profile.active_backlogs} active, {profile.previous_backlogs} cleared", "pass"))

    # ---- location / remote (soft signals, never a hard fail unless clearly off) ----
    remote_type = (facts.get("remote_type") or "").lower()
    if remote_type == "onsite" and not profile.open_to_relocation and profile.preferred_locations:
        location = (facts.get("location") or "").lower()
        if not any(p.lower() in location for p in profile.preferred_locations):
            checks.append(_check("location", f"onsite at {facts.get('location') or '?'}",
                                 ", ".join(profile.preferred_locations[:3]), "unknown",
                                 "Onsite outside preferred cities; relocation is off"))
            unknowns += 1

    # ---- fraud guard: a flagged posting is never 'eligible' ----
    fraud = facts.get("fraud_flags") or []
    if isinstance(fraud, str):
        import json
        try:
            fraud = json.loads(fraud) if fraud.strip() else []
        except ValueError:
            fraud = []
    for f in fraud:
        checks.append(_check("trust", "verified employer application channel", f, "fail",
                             "Resolve the warning before applying"))
        fails += 1

    status = ("not_eligible" if fails else
              "eligible" if not unknowns else
              "likely_eligible" if len(checks) - fails - unknowns >= 1 or not checks else
              "needs_verification")
    if unknowns and status == "likely_eligible" and fails == 0 and not any(c.result == "pass" for c in checks):
        status = "needs_verification"
    return EligibilityResult(status=status, checks=checks)  # type: ignore[arg-type]


def row_to_facts(row) -> dict:
    """sqlite Row -> plain dict with only the keys evaluate() cares about."""
    keys = ("batch_years", "experience_min", "experience_max", "min_cgpa", "min_percentage",
            "backlog_policy", "remote_type", "location", "fraud_flags", "employment_type",
            "fresher_flag", "salary_min", "salary_max", "stipend")
    d = {k: row[k] for k in keys if k in row.keys()} if hasattr(row, "keys") else dict(row)
    return d


def status_priority(s: str) -> int:
    return {"not_eligible": 0, "needs_verification": 1, "likely_eligible": 2, "eligible": 3}.get(s, 1)
