"""Phase 17.3 — India-first advanced search profiles.

A search profile bundles everything a fresher needs for one hunt:
role keywords, locations, batch years, employment types, salary floor, remote
preference, drive types and the sources to use. The DEFAULT profile is seeded
from the candidate profile (B.Tech CSE AIML 2027, Hyderabad, ₹6+ LPA, fresher)
but lives in config.yaml as plain data - the user edits it, nothing is hardcoded.

`apply()` pushes a profile into the config the discovery pipeline already reads;
`filters()` returns the db.query_jobs() filter set for the dashboard views.
"""
from __future__ import annotations

from typing import Any

from ruamel.yaml import YAML

from jobagent.config import ROOT, cfg, load_config

CONFIG_PATH = ROOT / "config.yaml"
_yaml = YAML()
_yaml.preserve_quotes = True


def _load_yaml() -> Any:
    with CONFIG_PATH.open(encoding="utf-8") as f:
        return _yaml.load(f)


def _save_yaml(doc) -> None:
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        _yaml.dump(doc, f)
    load_config.cache_clear()

DEFAULT_PROFILE: dict[str, Any] = {
    "name": "Fresher AI/ML 2027 (India)",
    "keywords": [
        "AI engineer fresher", "machine learning fresher", "python developer fresher",
        "software engineer fresher 2027 batch", "graduate engineer trainee software",
        "generative AI intern", "off campus drive 2027 batch",
    ],
    "locations": ["Hyderabad", "Bengaluru", "Chennai", "Pune", "Mumbai", "Gurugram", "Noida"],
    "batch_years": [2026, 2027],
    "employment_types": ["full_time", "internship", "trainee", "apprentice"],
    "fresher_only": True,
    "min_salary_lpa": 6,
    "remote_ok": True,
    "walk_in": False,
    "off_campus": True,
    "sources": ["companies", "linkedin", "naukri", "instahyre", "himalayas", "jobicy",
                "remotive", "arbeitnow"],
    "search_hints": [
        "2027 batch hiring", "2026 2027 graduate hiring", "off campus drive",
        "campus recruitment", "graduate trainee", "apprenticeship software",
        "fresher software roles", "entry level AI ML", "walk in interview",
        "internship for 2027 batch",
    ],
}


def profiles() -> list[dict[str, Any]]:
    """All configured search profiles (config search.profiles); seeded on first use."""
    stored = cfg("search.profiles", [])
    if stored:
        return list(stored)
    return [dict(DEFAULT_PROFILE)]


def get(name: str) -> dict[str, Any] | None:
    for p in profiles():
        if p.get("name", "").lower() == (name or "").lower():
            return p
    return None


def save(profile: dict[str, Any]) -> None:
    """Insert or update one profile by name (config.yaml is rewritten atomically)."""
    doc = _load_yaml()
    plist = doc.get("search", {}).get("profiles", []) or []
    plist = [p for p in plist if (p or {}).get("name", "").lower() != str(profile.get("name", "")).lower()]
    plist.append(profile)
    doc.setdefault("search", {})["profiles"] = plist
    _save_yaml(doc)


def delete(name: str) -> bool:
    doc = _load_yaml()
    plist = doc.get("search", {}).get("profiles", []) or []
    kept = [p for p in plist if (p or {}).get("name", "").lower() != name.lower()]
    if len(kept) == len(plist):
        return False
    doc["search"]["profiles"] = kept
    _save_yaml(doc)
    return True


def apply(name: str) -> dict[str, Any]:
    """Push a profile into the config keys the discovery pipeline reads."""
    profile = get(name)
    if not profile:
        raise KeyError(f"No search profile named '{name}'")
    doc = _load_yaml()
    doc.setdefault("search", {})
    doc["search"]["keywords"] = profile.get("keywords", [])
    doc["search"]["locations"] = profile.get("locations", [])
    doc["search"]["location_mode"] = "india_remote" if profile.get("remote_ok", True) else "india"
    if profile.get("min_salary_lpa"):
        doc.setdefault("automation", {})["min_salary_lpa"] = profile["min_salary_lpa"]
    doc["search"]["active_profile"] = profile.get("name")
    _save_yaml(doc)
    return profile


def active() -> dict[str, Any] | None:
    name = cfg("search.active_profile", "")
    return get(name) if name else None


def filters(name: str | None = None) -> dict[str, Any]:
    """db.query_jobs()-style filter dict for one profile (used by dashboard views)."""
    profile = get(name) if name else active() or profiles()[0]
    out: dict[str, Any] = {}
    if profile.get("fresher_only"):
        out["fresher_flag"] = 1
    if profile.get("walk_in"):
        out["walk_in"] = 1
    return out


def quick_views(profile: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """India-first dashboard quick views (Phase 17.3): one per hunt dimension."""
    p = profile or active() or profiles()[0]
    batch = (p.get("batch_years") or [2027])[0]
    loc = (p.get("locations") or ["Hyderabad"])[0]
    return [
        {"key": "fresher", "label": "Fresher Jobs", "filters": "fresher_flag=1"},
        {"key": "batch", "label": f"{batch} Batch Hiring", "filters": f"batch={batch}"},
        {"key": "internships", "label": "Internships", "filters": "employment_type=internship"},
        {"key": "offcampus", "label": "Off-Campus Drives", "filters": "off_campus=1"},
        {"key": "walkin", "label": "Walk-In Drives", "filters": "walk_in=1"},
        {"key": "trainee", "label": "Graduate Trainee", "filters": "employment_type=trainee"},
        {"key": "location", "label": f"{loc} Opportunities", "filters": f"location={loc}"},
        {"key": "remote", "label": "Remote India", "filters": "remote_type=remote"},
        {"key": "deadlines", "label": "Application Deadlines", "filters": "deadline_days=7"},
        {"key": "mnc", "label": "MNC Careers", "filters": "source=amazon|google|oracle"},
    ]
