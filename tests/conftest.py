"""Shared fixtures: an isolated data dir + config for every test (no real data touched).

The app binds DATA_DIR/PROFILE_DIR/... at import time, so each test patches jobagent.config
and reloads the dependent modules - every test then works against its own temp tree.
"""
from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

RELOADABLE = ("jobagent.db", "jobagent.enrich", "jobagent.profile", "jobagent.prefit",
              "jobagent.llm", "jobagent.matcher", "jobagent.eligibility", "jobagent.analytics",
              "jobagent.ai_services", "jobagent.tasks", "jobagent.server")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Fresh app state in tmp_path with the example config loaded."""
    data = tmp_path / "data"
    profile = tmp_path / "profile"
    output = tmp_path / "output"
    for d in (data, profile, output):
        d.mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO / "config.example.yaml", REPO / "config.yaml")

    import jobagent.config as config
    config.load_config.cache_clear()          # never inherit a previous test's config edits
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "PROFILE_DIR", profile)
    monkeypatch.setattr(config, "OUTPUT_DIR", output)
    monkeypatch.setattr(config, "MASTER_RESUME_JSON", profile / "master_resume.json")
    monkeypatch.setattr(config, "BASE_RESUME_PDF", profile / "source_resume.pdf")

    for name in RELOADABLE:
        importlib.reload(importlib.import_module(name))

    yield tmp_path

    (REPO / "config.yaml").unlink(missing_ok=True)
    config.load_config.cache_clear()
    for name in RELOADABLE:
        importlib.reload(importlib.import_module(name))
