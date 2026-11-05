"""Deterministic India-first enrichment tests - pure text parsing, no network."""
from __future__ import annotations

from jobagent.enrich import (detect_fraud, enrich, parse_academics, parse_batches,
                             parse_deadline, parse_employment_type, parse_experience,
                             parse_remote_type, parse_salary)


def test_salary_single_lpa():
    lo, hi, raw, stipend = parse_salary("Salary: 3.36 LPA CTC")
    assert (lo, hi) == (3.36, 3.36)
    assert stipend is None


def test_salary_range():
    lo, hi, _, _ = parse_salary("CTC: 12 - 18 LPA. Hybrid, Bengaluru.")
    assert (lo, hi) == (12.0, 18.0)


def test_salary_rupee_range():
    lo, hi, _, _ = parse_salary("₹6 - ₹9 LPA depending on experience")
    assert (lo, hi) == (6.0, 9.0)


def test_salary_rupees_lakhs():
    lo, hi, _, _ = parse_salary("Rs. 4.5 - 6 Lakhs per annum")
    assert (lo, hi) == (4.5, 6.0)


def test_salary_not_confused_with_years():
    # "0-2 years" must never parse as salary
    lo, hi, _, _ = parse_salary("0-2 years of experience required, Python")
    assert lo is None and hi is None


def test_stipend_monthly():
    lo, hi, raw, stipend = parse_salary("6-month internship, stipend of Rs 15,000 per month")
    assert stipend == 15000
    assert lo is None      # a stipend is not a CTC claim


def test_stipend_bare_number():
    _, _, _, stipend = parse_salary("Stipend: 20000/month during the internship")
    assert stipend == 20000


def test_salary_implausible_rejected():
    lo, hi, _, _ = parse_salary("package 500 LPA for the right candidate")
    assert lo is None      # 500 "LPA" is noise, not a salary


def test_experience_range():
    assert parse_experience("0-2 years", "full_time") == (0.0, 2.0)
    assert parse_experience("Requires 3+ years of Python", "full_time") == (3.0, 6.0)
    assert parse_experience("Fresher role, no experience needed", "full_time") == (0.0, None)
    assert parse_experience("", "internship") == (0.0, None)
    assert parse_experience("We build things", "full_time") == (None, None)


def test_employment_type():
    assert parse_employment_type("AI/ML Intern", "6 month internship") == "internship"
    assert parse_employment_type("Graduate Engineer Trainee", "join our GET program") == "trainee"
    assert parse_employment_type("Apprentice - DevOps", "apprenticeship program") == "apprentice"
    assert parse_employment_type("Part-time Tutor", "weekend work") == "part_time"
    assert parse_employment_type("Software Engineer", "build products") == "full_time"


def test_remote_type():
    assert parse_remote_type("SDE", "fully remote team, work from home") == "remote"
    assert parse_remote_type("SDE", "hybrid - 3 days office, Bengaluru") == "hybrid"
    assert parse_remote_type("SDE", "work from office, Hyderabad") == "onsite"


def test_batches_all_formats():
    assert parse_batches("B.Tech CS/IT/ECE, 2026, 2027 and 2028 batch") == [2026, 2027, 2028]
    assert parse_batches("eligible for 2026 and 2027 batches") == [2026, 2027]
    assert parse_batches("the batch of 2025") == [2025]
    assert parse_batches("2025-2027 batches may apply") == [2025, 2026, 2027]
    assert parse_batches("2027 passouts eligible") == [2027]
    assert parse_batches("ML role for 2027 batch") == [2027]
    assert parse_batches("senior python developer") == []


def test_academics():
    assert parse_academics("Minimum 6.0 CGPA, 60% throughout academics, no active backlogs") == \
        (6.0, 60.0, "no_active_backlogs")
    assert parse_academics("no backlogs at the time of joining") == (None, None, "no_backlogs")
    assert parse_academics("8.5 CGPA required") == (8.5, None, "")


def test_deadline_formats():
    assert parse_deadline("Last date to apply: 20 Jan 2027") == "2027-01-20"
    assert parse_deadline("apply before 5th February 2026") == "2026-02-05"
    assert parse_deadline("deadline 01/03/2026") == "2026-03-01"
    assert parse_deadline("join immediately") is None


def test_full_enrich_tcs_drive():
    f = enrich(
        "Assistant System Engineer (Off Campus Drive)",
        """Tata Consultancy Services Off Campus Drive 2027
        Eligibility: B.Tech CS/IT/ECE, 2026, 2027 and 2028 batch
        Minimum 60% throughout academics, 6.0 CGPA. No active backlogs.
        Salary: 3.36 LPA CTC. Experience: 0-2 years.
        Last date to apply: 20 Jan 2027. Work from office, Hyderabad.""",
        "TCS", "https://www.tcs.com/careers",
    )
    assert f.salary_min_lpa == 3.36
    assert f.batch_years == [2026, 2027, 2028]
    assert f.min_cgpa == 6.0 and f.min_percentage == 60.0
    assert f.backlog_policy == "no_active_backlogs"
    assert f.deadline == "2027-01-20"
    assert f.fresher_flag and f.off_campus and not f.walk_in
    assert f.experience_min == 0.0 and f.experience_max == 2.0
    assert f.fraud_flags == []


def test_fraud_payment_request():
    flags = detect_fraud("https://forms.example.com", "CareersPro",
                         "Pay registration fee of Rs 500 to apply. Guaranteed job, no interview.")
    assert any("payment" in f or "registration" in f for f in flags)


def test_fraud_free_mail_apply_link():
    flags = detect_fraud("https://apply.careerspro2024@gmail.com", "CareersPro", "Send your resume")
    assert any("free email" in f for f in flags)


def test_fraud_official_host_ok():
    assert detect_fraud("https://boards.greenhouse.io/acme/jobs/1", "Acme", "Come work with us") == []
    assert detect_fraud("https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite",
                        "NVIDIA", "GPU engineers wanted") == []
