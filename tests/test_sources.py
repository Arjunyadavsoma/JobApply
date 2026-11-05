"""Job-source tests: new adapters parse correctly with mocked HTTP; India registry seeds."""
from __future__ import annotations

import json

import httpx
import pytest

from jobagent.sources import india_companies
from jobagent.sources.arbeitnow import search as arbeitnow_search
from jobagent.sources.remotive import search as remotive_search


def _client(handler):
    transport = httpx.MockTransport(handler)
    return httpx.Client(transport=transport)


def test_arbeitnow_parses():
    payload = {"data": [
        {"title": "ML Engineer", "company_name": "DeepL", "slug": "deepl-ml-1",
         "location": "Berlin / Remote", "url": "https://x/1", "remote": True,
         "tags": ["python", "pytorch"], "created_at": 1700000000},
        {"title": "", "company_name": "NoTitle", "slug": "bad", "url": "", "remote": False,
         "tags": [], "created_at": 0},
    ], "links": {"next": None}}
    http = _client(lambda req: httpx.Response(200, json=payload))
    jobs = arbeitnow_search(1, http)
    assert len(jobs) == 1
    j = jobs[0]
    assert j.company == "DeepL" and j.source == "arbeitnow"
    assert j.description and "python" in j.description


def test_arbeitnow_handles_http_error():
    http = _client(lambda req: httpx.Response(500))
    assert arbeitnow_search(1, http) == []


def test_remotive_parses_and_dedups():
    def handler(req):
        assert "search" in str(req.url)
        return httpx.Response(200, json={"jobs": [
            {"id": 1, "title": "Remote Python Dev", "company_name": "Acme",
             "candidate_required_location": "Anywhere", "url": "https://r/1",
             "description": "python and ml", "publication_date": "2026-01-02"},
            {"id": 1, "title": "Duplicate", "company_name": "Acme", "url": "https://r/1",
             "candidate_required_location": "", "description": "", "publication_date": ""},
        ]})
    http = _client(handler)
    jobs = remotive_search(1, http, ["python"])
    assert len(jobs) == 1 and jobs[0].source == "remotive"
    assert jobs[0].location == "Anywhere"


def test_india_registry_seeds():
    cfg_in = {}
    out = india_companies.seed_config(cfg_in)
    assert any("Microsoft" in p for p in out["portals"])
    assert any("Amazon" in p for p in out["portals"])
    assert "razorpay" in out["greenhouse"]
    # user entries are preserved, no duplicates
    out2 = india_companies.seed_config({"portals": ["Microsoft | https://careers.microsoft.com/"]})
    assert sum(1 for p in out2["portals"] if "Microsoft" in p) == 1


def test_registry_entries_are_https():
    for company, url, _kind in india_companies.REGISTRY:
        assert url.startswith("https://"), f"{company} registry URL must be https: {url}"
