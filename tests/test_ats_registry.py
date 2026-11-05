"""Phase 17.5 — ATS registry tests."""
from __future__ import annotations

from jobagent import ats_registry as ar


def test_detect_known_platforms():
    cases = {
        "https://boards.greenhouse.io/acme/jobs/123": "greenhouse",
        "https://job-boards.eu.greenhouse.io/embed/job_app?for=acme&token=1": "greenhouse",
        "https://jobs.lever.co/acme/abcd": "lever",
        "https://jobs.ashbyhq.com/acme/xyz": "ashby",
        "https://acme.wd1.myworkdayjobs.com/en-US/careers/job/xyz": "workday",
        "://invalid": None,  # placeholder, replaced below
    }
    del cases["://invalid"]
    for url, key in cases.items():
        d = ar.detect(url)
        assert d["platform"] == key, url
        assert d["confidence"] >= 0.9


def test_detect_smartrecruiters_icims_taleo_oracle():
    assert ar.detect("https://careers.smartrecruiters.com/acme")["platform"] == "smartrecruiters"
    assert ar.detect("https://acme.icims.com/jobs/1")["platform"] == "icims"
    assert ar.detect("https://acme.taleo.net/careersection/1/jobdetail")["platform"] == "taleo"
    assert ar.detect("https://acme.oraclecloud.com/hcmUI/CandidateExperience")["platform"] == "oracle_recruiting"
    assert ar.detect("https://acme.eightfold.ai/careers")["platform"] == "eightfold"
    assert ar.detect("https://acme.successfactors.com/careers")["platform"] == "successfactors"


def test_detect_india_portals():
    assert ar.detect("https://www.naukri.com/job-listings-1")["platform"] == "naukri"
    assert ar.detect("https://www.instahyre.com/job/1")["platform"] == "instahyre"
    assert ar.detect("https://www.linkedin.com/jobs/view/1")["platform"] == "linkedin"


def test_detect_generic_fallback():
    d = ar.detect("https://careers.someunknownstartup.dev/apply/1")
    assert d["platform"] == "generic"
    assert d["confidence"] < 0.5
    assert "semantic" in d["notes"].lower()


def test_quirks_content():
    q = ar.quirks("workday")
    assert q["multi_step"] is True
    assert any("data-automation-id" in s for s in q["submit_selectors"])
    assert q["notes"]
    q2 = ar.quirks("greenhouse")
    assert q2["multi_step"] is True
    q3 = ar.quirks("naukri")
    assert "never" in q3["notes"].lower() or "never auto" in q3["notes"].lower()


def test_known_platforms_covers_phase17_list():
    keys = {p["key"] for p in ar.known_platforms()}
    required = {"greenhouse", "lever", "ashby", "workday", "smartrecruiters", "icims",
                "taleo", "darwinbox", "zoho_recruit", "successfactors", "oracle_recruiting",
                "eightfold", "phenom"}
    assert required <= keys
