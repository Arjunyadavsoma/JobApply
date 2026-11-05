"""Phase 17.4 — verification engine tests (offline: mocked HTTP)."""
from __future__ import annotations

import json

import httpx
import pytest

from jobagent import db, verify


def _row(**kw):
    """A jobs-table-like row with sensible defaults."""
    base = {
        "id": "x", "source": "greenhouse", "company": "Acme", "title": "SDE",
        "url": "https://boards.greenhouse.io/acme/jobs/1", "apply_url": "https://boards.greenhouse.io/acme/jobs/1",
        "description": "d", "posted_at": "2026-11-01", "discovered_at": "2026-11-04",
        "deadline": "", "salary_min": None, "salary_max": None, "salary_text": "",
        "fresher_flag": 0, "experience_min": None, "fraud_flags": "[]", "status": "new",
        "first_verified_at": None, "last_verified_at": None,
    }
    base.update(kw)
    return base


def _client(responses: dict[str, tuple[int, str]]):
    """httpx.MockTransport keyed by URL substring."""
    def handler(request: httpx.Request) -> httpx.Response:
        for frag, (status, body) in responses.items():
            if frag in str(request.url):
                return httpx.Response(status, text=body, request=request)
        return httpx.Response(404, text="not found", request=request)
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_open_page_with_apply_button_is_verified():
    row = _row()
    http = _client({"greenhouse": (200, "<html><body>Apply now for this job. We are hiring.</body></html>")})
    res = verify.verify_job_row(row, http=http)
    assert res.state == "verified_open"
    assert res.http_status == 200
    assert res.quality_score >= 60
    assert res.components["availability"] == 20
    assert res.components["page_plausibility"] >= 14


def test_bare_200_is_never_verified_open():
    """Requirement: HTTP 200 alone must not mark a job verified open."""
    row = _row()
    http = _client({"greenhouse": (200, "<html><body>welcome to our blog about food</body></html>")})
    res = verify.verify_job_row(row, http=http)
    assert res.state in ("probably_open", "needs_manual")
    assert res.state != "verified_open"


def test_404_is_expired():
    row = _row()
    http = _client({"greenhouse": (404, "gone")})
    res = verify.verify_job_row(row, http=http)
    assert res.state == "expired"
    assert res.components["availability"] == 0


def test_410_is_expired():
    row = _row()
    http = _client({"greenhouse": (410, "This job is closed")})
    res = verify.verify_job_row(row, http=http)
    assert res.state == "expired"


def test_position_filled_marker_expires():
    row = _row()
    http = _client({"greenhouse": (200, "Sorry, this position has been filled. Apply now to other roles.")})
    res = verify.verify_job_row(row, http=http)
    assert res.state == "expired"


def test_network_failure_is_unavailable():
    def handler(request):
        raise httpx.ConnectError("no route", request=request)
    row = _row()
    http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    res = verify.verify_job_row(row, http=http)
    assert res.state == "unavailable"


def test_free_mail_apply_domain_is_suspicious():
    row = _row(apply_url="https://forms.gmail.com/apply")
    http = _client({"gmail": (200, "apply now")})
    res = verify.verify_job_row(row, http=http)
    assert res.state == "suspicious"
    assert any("free mail" in f for f in res.fraud_flags)


def test_shortener_and_ip_flagged():
    row = _row(apply_url="https://bit.ly/xyz")
    res = verify.verify_job_row(row, http=_client({}))
    assert res.state == "suspicious" or res.components["domain_trust"] == 0
    row2 = _row(apply_url="http://123.45.67.89/apply")
    res2 = verify.verify_job_row(row2, http=_client({}))
    assert res2.components["domain_trust"] == 0
    assert any("raw IP" in f for f in res2.fraud_flags)


def test_lookalike_domain_flagged():
    row = _row(company="Microsoft", apply_url="https://careers.micros0ft-hr.com/apply")
    res = verify.verify_job_row(row, http=_client({}))
    assert res.state == "suspicious"
    assert any("typo-squat" in f for f in res.fraud_flags)


def test_private_host_blocked_ssrf():
    for url in ("http://localhost/apply", "http://127.0.0.1/apply", "http://192.168.1.5/apply",
                "http://[::1]/apply"):
        row = _row(apply_url=url)
        res = verify.verify_job_row(row, http=_client({}))
        assert res.state == "suspicious", url


def test_past_deadline_expires_without_fetch():
    row = _row(deadline="2026-01-01")
    res = verify.verify_job_row(row, http=_client({}))
    assert res.state == "expired"
    assert res.method == "deadline"


def test_salary_sanity():
    good = verify._salary_sanity(_row(salary_min=6.0, salary_max=12.0))
    assert good[0] == 10
    inverted = verify._salary_sanity(_row(salary_min=12.0, salary_max=6.0))
    assert inverted[0] == 0
    absurd = verify._salary_sanity(_row(salary_min=500.0, salary_max=600.0))
    assert absurd[0] == 1


def test_company_domain_match_scores_full_trust():
    row = _row(company="Razorpay", apply_url="https://razorpay.com/careers/sde-1")
    http = _client({"razorpay": (200, "apply for this job")})
    res = verify.verify_job_row(row, http=http)
    assert res.components["domain_trust"] == 20


def test_quality_score_range_and_persist(env):
    row = _row(apply_url="https://boards.greenhouse.io/acme/jobs/2")
    db.upsert_jobs([db.Job(source="greenhouse", external_id="acme:2", company="Acme", title="SDE II",
                           url=row["url"], apply_url=row["apply_url"], description="x")])
    jid = db.job_id("greenhouse", "acme:2")
    http = _client({"jobs/2": (200, "Apply now. This position is open.")})
    res = verify.verify_job_row(db.get(jid), http=http)
    verify.persist(jid, res)
    after = db.get(jid)
    assert after["verification_state"] == "verified_open"
    assert 0 <= after["quality_score"] <= 100
    assert after["first_verified_at"]
    assert after["last_verified_at"]
    ev = db.events_for(jid)
    assert any(e["kind"] == "verify" for e in ev)


def test_fraud_flags_merged_on_persist(env):
    db.upsert_jobs([db.Job(source="x", external_id="c:1", company="C", title="T",
                           url="https://bit.ly/a", apply_url="https://bit.ly/a", description="x")])
    jid = db.job_id("x", "c:1")
    res = verify.verify_job_row(db.get(jid), http=_client({}))
    verify.persist(jid, res)
    after = db.get(jid)
    flags = json.loads(after["fraud_flags"] or "[]")
    assert flags, "shortener flag should persist"


def test_verify_batch_runs_and_counts(env, monkeypatch):
    for i in range(3):
        db.upsert_jobs([db.Job(source="greenhouse", external_id=f"acme:{i}", company="Acme",
                               title=f"SDE {i}", url=f"https://boards.greenhouse.io/acme/jobs/{i}",
                               apply_url=f"https://boards.greenhouse.io/acme/jobs/{i}",
                               description="job posting")])
    real_client_cls = httpx.Client
    inner = _client({"greenhouse": (200, "Apply now for this job")})   # built BEFORE patching

    class FakeCtx:
        def __init__(self, *a, **k):
            pass
        def __enter__(self):
            return inner
        def __exit__(self, *a):
            return False
    # Only the client that verify_batch constructs for itself; the mock above is pre-built.
    import jobagent.verify as V
    monkeypatch.setattr(V.httpx, "Client", lambda *a, **k: FakeCtx())
    try:
        checked, counts = verify.verify_batch(limit=5)
    finally:
        monkeypatch.setattr(V.httpx, "Client", real_client_cls)
    assert checked == 3
    assert counts.get("verified_open") == 3
