"""Phase 18.2 — form intelligence engine tests (deterministic mapping, no network)."""
from __future__ import annotations

import pytest

from jobagent.form_intel import (
    BY_CANONICAL, FieldMapping, ai_map_fields, apply_ai_fallback, map_field, map_fields,
    resolve_profile_value, summary,
)
from jobagent.models import CandidateProfile


@pytest.fixture()
def profile() -> CandidateProfile:
    return CandidateProfile(
        full_name="Arjun Yadav", email="arjun@example.com", phone="+91 9876543210",
        current_location="Hyderabad, India",
        graduation_year=2027, degree="B.Tech",
        branch="CSE (AI & ML)", college="XYZ Institute of Technology",
        university="JNTUH", cgpa=8.6,
        school_tenth_name="Delhi Public School", tenth_percentage=92.0,
        school_twelfth_name="Narayana Junior College", twelfth_percentage=94.5,
        intermediate_qualification="MPC",
        candidate_type="fresher", total_experience_years=0.0,
        internship_experience="1 ML internship (2 months)",
        work_authorization="Indian citizen; authorized to work in India",
        open_to_relocation=True, expected_ctc_lpa=6.0, notice_period="Immediate",
        active_backlogs=0, previous_backlogs=0, education_gap_months=0,
        preferred_locations=["Hyderabad", "Bengaluru"],
    )


def _meta(label="", _id=None, **kw):
    if _id is not None:
        base_id = _id
    else:
        base_id = "fr0_" + str(_meta.n)
        _meta.n += 1
    base = {"id": base_id, "kind": "text", "label": label, "name": "",
            "required": False, "value": ""}
    base.update(kw)
    if not _id:
        pass
    return base


_meta.n = 0


# ------------------------------------------------------------------ spec examples

@pytest.mark.parametrize("label,canonical,confidence", [
    ("First Name", "first_name", "high"),
    ("Given Name", "first_name", "high"),
    ("Legal First Name", "first_name", "high"),
    ("Current CTC (in LPA)", "current_ctc", "high"),
    ("Present Salary", "current_ctc", "high"),
    ("Current Compensation", "current_ctc", "high"),
    ("Graduation Year", "graduation_year", "high"),
    ("Passing Year", "graduation_year", "high"),
    ("Expected Graduation", "graduation_year", "high"),
    ("Total Experience (in years)", "experience", "high"),
    ("Years of Experience", "experience", "high"),
    ("10th Percentage", "tenth_percentage", "high"),
    ("12th Percentage", "twelfth_percentage", "high"),
    ("Active Backlogs (if any)", "active_backlogs", "high"),
    ("Historical Backlogs", "historical_backlogs", "high"),
    ("Notice Period", "notice_period", "high"),
    ("Upload your resume", "resume_file", "high"),
    ("Why do you want to work here?", "why_this_company", "high"),
])
def test_spec_label_examples(label, canonical, confidence, profile):
    m = resolve_profile_value(map_field(_meta(label)), profile)
    assert m.canonical == canonical
    assert m.confidence == confidence


def test_boolean_question_labels(profile):
    m = resolve_profile_value(map_field(_meta("Are you legally authorized to work in India?")), profile)
    assert m.canonical == "work_authorization"
    assert m.confidence == "high"
    m2 = resolve_profile_value(map_field(_meta("Are you willing to relocate?")), profile)
    assert m2.canonical == "relocation_preference"
    assert m2.profile_value == "Yes"


# ------------------------------------------------------------------ sensitive fields

@pytest.mark.parametrize("label", [
    "Aadhaar Number", "PAN Card Number", "I authorize the company to process my data",
    "I agree to the terms and conditions", "Declaration", "Gender",
    "Date of Birth", "Caste (for reservation)", "Salary History",
])
def test_sensitive_never_autofilled(label, profile):
    m = resolve_profile_value(map_field(_meta(label)), profile)
    assert m.sensitive, f"{label} should be sensitive"
    assert m.action == "needs_human"
    assert m.profile_value is None


# ------------------------------------------------------------------ confidence levels

def test_medium_confidence_via_name_attribute(profile):
    m = resolve_profile_value(map_field(_meta(label="?", name="firstName")), profile)
    assert m.canonical == "first_name"
    assert m.confidence == "medium"
    assert m.action == "fill_flagged"          # filled but flagged for review


def test_medium_confidence_via_placeholder(profile):
    m = resolve_profile_value(map_field(_meta(label="?", placeholder="Enter your email address")), profile)
    assert m.canonical == "email"
    assert m.confidence == "medium"


def test_high_confidence_label_beats_name(profile):
    m = resolve_profile_value(map_field(_meta(label="Last Name", name="firstNameInput")), profile)
    assert m.canonical == "last_name"          # label wins over name
    assert m.confidence == "high"


def test_unmapped_field_is_low(profile):
    m = resolve_profile_value(map_field(_meta("What is your favourite colour?")), profile)
    assert m.canonical == ""
    assert m.confidence == "low"
    assert m.action == "ai_or_ask"


# ------------------------------------------------------------------ value resolution

def test_values_never_invented(profile):
    p2 = profile.model_copy(update={"tenth_percentage": None, "expected_ctc_lpa": None,
                                    "notice_period": ""})
    for label, key in (("10th Percentage", "tenth_percentage"), ("Expected CTC", "expected_ctc"),
                       ("Notice Period", "notice_period")):
        m = resolve_profile_value(map_field(_meta(label)), p2)
        assert m.canonical == key
        assert m.profile_value is None, f"{key} must stay unknown"
        assert m.action == "needs_human"


def test_name_splitting(profile):
    first = resolve_profile_value(map_field(_meta("First Name")), profile)
    last = resolve_profile_value(map_field(_meta("Last Name")), profile)
    assert first.profile_value == "Arjun"
    assert last.profile_value == "Yadav"


def test_select_option_matching(profile):
    meta = _meta("Preferred Location", kind="select",
                 options=["Bengaluru", "Chennai", "Hyderabad", "Pune"])
    m = resolve_profile_value(map_field(meta), profile, meta)
    assert m.profile_value == "Hyderabad"      # single-select takes ONE option


def test_boolean_with_yes_no_options(profile):
    meta = _meta("Are you a fresher?", kind="radio", options=["Yes", "No"])
    m = resolve_profile_value(map_field(meta), profile, meta)
    assert m.canonical == "fresher_status"
    assert m.profile_value == "Yes"


def test_work_authorization_statement_profile(profile):
    meta = _meta("Are you legally authorized to work in India?", kind="radio",
                 options=["Yes", "No"])
    m = resolve_profile_value(map_field(meta), profile, meta)
    assert m.canonical == "work_authorization"
    assert m.profile_value == "Yes"            # profile states authorization explicitly


def test_ctc_range_options(profile):
    meta = _meta("Expected CTC", kind="select",
                 options=["3-5 LPA", "5-8 LPA", "8-12 LPA"])
    m = resolve_profile_value(map_field(meta), profile, meta)
    assert m.profile_value == "5-8 LPA"         # 6.0 falls in 5-8


def test_percentage_and_year_formatting(profile):
    pct = resolve_profile_value(map_field(_meta("10th Percentage")), profile)
    assert pct.profile_value == 92.0
    yr = resolve_profile_value(map_field(_meta("Graduation Year")), profile)
    assert yr.profile_value == 2027
    assert isinstance(yr.profile_value, int)


def test_fresher_backlogs_default_zero_is_a_value(profile):
    m = resolve_profile_value(map_field(_meta("Any active backlogs?")), profile)
    assert m.profile_value == 0                 # 0 backlogs is real data, not unknown


# ------------------------------------------------------------------ full page + summary

def test_map_fields_full_page(profile):
    metas = [
        _meta("First Name"), _meta("Last Name"), _meta("Email Address"),
        _meta("10th Percentage"), _meta("Current CTC"), _meta("Aadhaar Number"),
        _meta("What makes you unique?", kind="textarea"),
        _meta("I agree to the privacy policy", kind="checkbox"),
    ]
    s = summary(map_fields(metas, profile))
    assert s["counts"]["auto_fill"] >= 4          # identity + academics + contact
    assert s["counts"]["needs_human"] == 3        # aadhaar + privacy consent + current CTC (unknown)
    assert s["counts"]["ai_or_ask"] == 1          # the free question
    # sensitive labels show up in needs_human with their original label
    labels = [f["label"] for f in s["needs_human"]]
    assert "Aadhaar Number" in labels


# ------------------------------------------------------------------ AI fallback rules

def test_ai_never_overrides_registry_or_profile(profile, monkeypatch):
    def fake_structured(schema, system=None, content=None, effort="low", task="", **kw):
        class _F:
            def __init__(self, field_id, canonical, confidence):
                self.field_id, self.canonical, self.confidence = field_id, canonical, confidence

        class _R:
            fields = [_F("x1", "first_name", "high"),        # illegal upgrade attempt
                      _F("x2", "current_ctc", "medium"),     # tries to override
                      _F("x3", "sensitive", "medium"),
                      _F("x4", "free_text", "low")]
        return _R()

    import jobagent.form_intel as fi
    import jobagent.llm as llm
    monkeypatch.setattr(llm, "structured", fake_structured)

    metas = [_meta("?? mystery", _id="x1"), _meta("?? mystery", _id="x2"),
             _meta("?? mystery", _id="x3"), _meta("?? mystery", _id="x4")]
    mappings = [resolve_profile_value(map_field(m), profile, m) for m in metas]
    ai = ai_map_fields(metas)
    assert ai["x1"]["confidence"] == "medium"       # 'high' from AI is capped to medium
    mappings = apply_ai_fallback(mappings, ai, profile, metas)
    by_id = {m.field_id: m for m in mappings}
    # AI classified as first_name → value still resolved from the PROFILE, not from AI
    assert by_id["x1"].canonical == "first_name"
    assert by_id["x1"].profile_value == "Arjun"
    assert by_id["x1"].source == "ai"
    # sensitive classification strips the canonical (never auto-filled)
    assert by_id["x3"].canonical == ""
    assert by_id["x3"].action == "needs_human"
    # free_text stays unmapped for the LLM answer agent
    assert by_id["x4"].canonical == ""


def test_ai_fallback_failure_is_safe(profile, monkeypatch):
    import jobagent.llm as llm

    def boom(*a, **kw):
        raise RuntimeError("groq down")

    monkeypatch.setattr(llm, "structured", boom)
    mappings = [resolve_profile_value(map_field(_meta("Odd question?")), profile)]
    ai = ai_map_fields([_meta("Odd question?", _id="q9")])
    assert ai == {}                                  # graceful: field stays low confidence
    assert mappings[0].action == "ai_or_ask"


def test_registry_has_india_fields():
    for key in ("tenth_school", "tenth_percentage", "twelfth_school", "twelfth_percentage",
                "intermediate_qualification", "diploma", "graduation_batch", "active_backlogs",
                "historical_backlogs", "education_gap", "current_ctc", "expected_ctc",
                "notice_period", "fresher_status", "internship_experience",
                "work_authorization", "relocation_preference", "experience"):
        assert key in BY_CANONICAL, f"missing India field {key}"
        assert BY_CANONICAL[key].sensitive is False
