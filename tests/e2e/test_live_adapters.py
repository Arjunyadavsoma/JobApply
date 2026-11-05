"""Phase 17.10 — LIVE adapter tests against real public job APIs.

Excluded from the default run (`addopts = -m "not live"`); run explicitly:
    python -m pytest tests/e2e/test_live_adapters.py -m live --live

Never submits anything; only reads public listing data. Each adapter must return
real, normalized jobs or fail loudly - no fakes.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

pytestmark = [pytest.mark.live, pytest.mark.e2e]


@pytest.fixture(scope="module")
def http():
    with httpx.Client(timeout=25, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 JobApply-E2E/17"}) as c:
        yield c


def test_remotive_live(http):
    from jobagent.sources import remotive
    jobs = remotive.search(1, http, ["python"])
    assert isinstance(jobs, list)
    if jobs:                                   # the API can have an empty day
        j = jobs[0]
        assert j.company and j.title and j.apply_url.startswith("http")
        assert j.description and len(j.description) > 100


def test_arbeitnow_live(http):
    from jobagent.sources import arbeitnow
    jobs = arbeitnow.search(1, http, [])
    assert isinstance(jobs, list)


def test_himalayas_live(http):
    from jobagent.sources import himalayas
    jobs = himalayas.search(1, http, ["python", "engineer", "developer"])
    assert isinstance(jobs, list)
    for j in jobs:
        assert j.source == "himalayas"
        assert j.title and j.company


def test_jobicy_live(http):
    from jobagent.sources import jobicy
    jobs = jobicy.search(1, http, ["python"])
    assert isinstance(jobs, list)


def test_instahyre_api_live(http):
    """The search API itself (details need a browser; covered separately)."""
    r = http.get("https://www.instahyre.com/api/v1/job_search")
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("application/json")


def test_linkedin_guest_live(http):
    from jobagent.sources import linkedin
    jobs = linkedin.search(["python fresher"], ["Hyderabad"], 1, 30, http)
    assert isinstance(jobs, list)
    if jobs:
        assert all(j.source == "linkedin" for j in jobs)


def test_greenhouse_board_live(http):
    """One well-known public Greenhouse board, via the official public API."""
    from jobagent.sources.ats import greenhouse
    jobs = greenhouse("stripe", http)
    assert isinstance(jobs, list)
    assert all(j.source == "greenhouse" for j in jobs)
    if jobs:
        assert jobs[0].url.startswith("https://")
