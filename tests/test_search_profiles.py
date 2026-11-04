"""Phase 17.3 — search profiles + extended job query filters."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
import sys
sys.path.insert(0, str(REPO))


@pytest.fixture()
def profile_env(env, monkeypatch):
    """env fixture + a fresh search_profiles module bound to the temp config."""
    import importlib
    import jobagent.config as config
    # config.yaml was copied into REPO by the env fixture; point our module at it
    import jobagent.search_profiles as sp
    monkeypatch.setattr(sp, "CONFIG_PATH", config.CONFIG_PATH)
    return sp


def test_default_profile_matches_candidate_defaults(profile_env):
    sp = profile_env
    profiles = sp.profiles()
    assert len(profiles) >= 1
    p = profiles[0]
    assert p["name"].startswith("Fresher")
    assert 2027 in p["batch_years"]
    assert "Hyderabad" in p["locations"]
    assert p["min_salary_lpa"] == 6
    assert any("python" in k for k in p["keywords"])
    # search hints cover the Phase 17.3 required hunt types
    hints = " ".join(p["search_hints"]).lower()
    for phrase in ("2027 batch", "off campus", "graduate trainee", "walk in", "intern"):
        assert phrase in hints or phrase.replace(" ", "") in hints.replace(" ", "")


def test_save_apply_delete_roundtrip(profile_env, monkeypatch):
    sp = profile_env
    sp.save({"name": "Data roles 2026", "keywords": ["data analyst fresher"],
             "locations": ["Pune"], "min_salary_lpa": 5})
    got = sp.get("data roles 2026")
    assert got and got["keywords"] == ["data analyst fresher"]

    applied = sp.apply("Data roles 2026")
    assert applied["locations"] == ["Pune"]
    from jobagent.config import cfg, load_config
    load_config.cache_clear()
    assert cfg("search.active_profile") == "Data roles 2026"
    assert cfg("search.keywords") == ["data analyst fresher"]
    assert cfg("automation.min_salary_lpa") == 5

    assert sp.delete("Data roles 2026") is True
    assert sp.get("data roles 2026") is None
    assert sp.delete("data roles 2026") is False


def test_quick_views_cover_india_navigation(profile_env):
    sp = profile_env
    views = sp.quick_views()
    labels = " ".join(v["label"].lower() for v in views)
    assert len(views) == 10
    for word in ("fresher", "batch", "internship", "off-campus", "walk-in", "remote", "deadline"):
        assert word in labels, word


def test_query_jobs_new_filters(env):
    from jobagent import db
    db.upsert_jobs([
        db.Job(source="t", external_id="1", company="A", title="AI Engineer",
               url="https://a.com/1", location="Hyderabad",
               description="Work on python and pytorch models. Apply now."),
        db.Job(source="t", external_id="2", company="B", title="Sales Manager",
               url="https://b.com/2", location="Delhi",
               description="Sell insurance. Apply now."),
    ])
    db.update(db.job_id("t", "1"), salary_max=8.0, experience_min=0, fresher_flag=1)
    db.update(db.job_id("t", "2"), salary_max=4.0, experience_min=3)

    by_loc = db.query_jobs(location="hyderabad")
    assert {r["title"] for r in by_loc} == {"AI Engineer"}

    by_skill = db.query_jobs(skills="pytorch")
    assert {r["title"] for r in by_skill} == {"AI Engineer"}

    by_salary = db.query_jobs(min_salary=6)
    assert {r["title"] for r in by_salary} == {"AI Engineer"}

    by_exp = db.query_jobs(max_experience=0)
    assert {r["title"] for r in by_exp} == {"AI Engineer"}

    db.update(db.job_id("t", "1"), remote_type="remote")
    remote = db.query_jobs(location="nowhere-ville")
    assert {r["title"] for r in remote} == {"AI Engineer"}   # remote roles pass any location
