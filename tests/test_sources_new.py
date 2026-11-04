"""Phase 17.2 — new source adapters (himalayas, jobicy) + external-link registry tests."""
from __future__ import annotations

import json

import httpx
import pytest

from jobagent.sources import external, himalayas, jobicy


def _client(responses):
    def handler(request: httpx.Request) -> httpx.Response:
        for frag, payload in responses.items():
            if frag in str(request.url):
                return httpx.Response(200, json=payload, request=request)
        return httpx.Response(404, json={}, request=request)
    return httpx.Client(transport=httpx.MockTransport(handler))


HIMALAYAS_PAGE = {
    "jobs": [
        {"guid": "g1", "title": "Python Developer", "companyName": "Acme", "excerpt": "python shop",
         "description": "<p>Build things</p>", "applicationLink": "https://acme.com/careers/1",
         "companySlug": "acme", "locationRestrictions": ["india"], "pubDate": "2026-11-01T00:00:00Z"},
        {"guid": "g2", "title": "Rust Engineer", "companyName": "Beta", "excerpt": "rust shop",
         "description": "rust", "applicationLink": "https://beta.com/2", "companySlug": "beta",
         "locationRestrictions": ["united states"], "pubDate": "2026-11-01T00:00:00Z"},
        {"guid": "g3", "title": "AI Engineer (Worldwide)", "companyName": "Gamma", "excerpt": "llm",
         "description": "llms", "applicationLink": "https://gamma.com/3", "companySlug": "gamma",
         "locationRestrictions": ["worldwide"], "pubDate": "2026-11-02T00:00:00Z",
         "currency": "USD", "salaryPeriod": "year", "minSalary": 40000, "maxSalary": 60000},
    ],
    "nextCursor": None,
}


def test_himalayas_filters_location_and_keywords():
    http = _client({"himalayas.app": HIMALAYAS_PAGE})
    jobs = himalayas.search(1, http, ["python", "ai"])
    assert len(jobs) == 2                       # US-locked g2 dropped
    titles = {j.title for j in jobs}
    assert "Python Developer" in titles
    india = [j for j in jobs if "India ok" in (j.location or "")]
    assert india, "India-restricted job should be tagged"
    g3 = next(j for j in jobs if j.company == "Gamma")
    assert "40,000" in (g3.description or "") or "40000" in (g3.description or "")
    assert g3.apply_url == "https://gamma.com/3"


def test_himalayas_pagination_stops_without_cursor():
    http = _client({"himalayas.app": HIMALAYAS_PAGE})
    jobs = himalayas.search(3, http, [])
    assert len(jobs) >= 1                       # worldwide ones pass with no keywords


JOBICY_PAGE = {
    "jobs": [
        {"id": "j1", "jobTitle": "ML Engineer", "companyName": "Acme", "jobDescription": "train models",
         "jobExcerpt": "ml", "jobGeo": "Worldwide", "url": "https://jobicy.com/jobs/1-ml",
         "pubDate": "2026-11-01", "salaryCurrency": "usd", "salaryPeriod": "year",
         "salaryMin": 50000, "salaryMax": 70000},
        {"id": "j2", "jobTitle": "Backend Dev", "companyName": "Beta", "jobDescription": "django",
         "jobGeo": "Europe only", "url": "https://jobicy.com/jobs/2-bd", "pubDate": "2026-11-01"},
    ]
}


def test_jobicy_filters_geo_and_keeps_salary():
    http = _client({"jobicy.com": JOBICY_PAGE})
    jobs = jobicy.search(1, http, ["ml"])
    assert len(jobs) == 1                       # Europe-only dropped
    j = jobs[0]
    assert j.company == "Acme" and j.apply_url.endswith("1-ml")
    assert "50000" in j.description


# ---------------------------------------------------------------- external-link registry

def test_every_external_source_has_reason_and_link():
    links = external.links_for(["python fresher"])
    assert len(links) >= 12
    for l in links:
        assert l["reason"], f"{l['key']} must document why it has no adapter"
        assert l["kind"] in ("portal", "campus", "ats")
        if l["search_url"]:
            assert l["search_url"].startswith("https://"), l["key"]


def test_external_links_substitute_keywords():
    links = external.links_for(["ai engineer"])
    by_key = {l["key"]: l for l in links}
    assert "ai+engineer" in by_key["naukri"]["search_url"]
    assert "ai+engineer" in by_key["foundit"]["search_url"]


def test_no_adapters_pretend_to_be_integrated():
    """Sources with real adapters (naukri/instahyre/linkedin) say so in their reason."""
    by_key = {s.key: s for s in external.SOURCES}
    assert "integrated" in by_key["naukri"].reason.lower()
    assert "integrated" in by_key["instahyre"].reason.lower()
    assert "integrated" in by_key["linkedin"].reason.lower()
    # Sources that are NOT integrated must explain the blocker honestly.
    assert "403" in by_key["freshersworld"].reason or "403" in by_key["foundit"].reason
