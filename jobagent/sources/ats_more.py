"""More career-site systems, via the same public JSON their job pages load (no login):

- Workday         https://<tenant>.wd<N>.myworkdayjobs.com/<site>   (keyword search per company)
- SmartRecruiters https://jobs.smartrecruiters.com/<CompanyId>      (keyword search per company)
- Workable        https://apply.workable.com/<slug>                 (all open jobs per company)

Workday and SmartRecruiters are searched with your search keywords, since big employers list thousands
of roles. Title and location filters run before job details are fetched, to keep requests low.
"""
from __future__ import annotations

import random
import re
import time
from urllib.parse import urlparse

import httpx

from jobagent.config import cfg
from jobagent.db import Job
from jobagent.sources.common import html_to_text
from jobagent.sources.location import keep_location

JSON = {"Accept": "application/json", "Content-Type": "application/json"}


def _title_ok(title: str) -> bool:
    t = title.lower()
    if any(x.lower() in t for x in cfg("search.title_exclude", [])):
        return False
    must = [x.lower() for x in cfg("search.title_must_include", [])]
    return not must or any(x in t for x in must)


def _location_ok(location: str, source: str) -> bool:
    return keep_location(location, source, cfg("search.location_mode", "any"))


def _pause(lo: float = 0.4, hi: float = 1.0) -> None:
    time.sleep(random.uniform(lo, hi))


# ---------------------------------------------------------------- Workday

def _workday_parts(url: str) -> tuple[str, str, str]:
    """careers URL -> (host, tenant, site). Accepts .../<site> and .../<lang>/<site>."""
    u = urlparse(url if "//" in url else f"https://{url}")
    parts = [p for p in u.path.split("/") if p]
    site = next((p for p in reversed(parts) if not re.fullmatch(r"[a-z]{2}-[A-Z]{2}", p)), "")
    return u.netloc, u.netloc.split(".")[0], site


def workday(entry: str, http: httpx.Client, keywords: list[str]) -> list[Job]:
    """`entry`: careers URL, optionally prefixed with a display name: "Morgan Stanley | https://..."."""
    name, _, url = entry.rpartition("|")
    url = url.strip()
    host, tenant, site = _workday_parts(url)
    company = name.strip() or tenant.title()
    if not site:
        raise ValueError("expected a URL like https://company.wd5.myworkdayjobs.com/SiteName")
    base = f"https://{host}/wday/cxs/{tenant}/{site}"
    pages = int(cfg("sources.workday_pages", 2))
    postings: dict[str, dict] = {}
    for kw in keywords or [""]:
        for n in range(pages):
            r = http.post(f"{base}/jobs", headers=JSON,
                          json={"appliedFacets": {}, "limit": 20, "offset": n * 20, "searchText": kw})
            r.raise_for_status()
            batch = r.json().get("jobPostings", [])
            for p in batch:
                postings.setdefault(p.get("externalPath", ""), p)
            if len(batch) < 20:
                break
            _pause()

    jobs = []
    for path, p in postings.items():
        title = p.get("title", "").strip()
        if not path or not _title_ok(title):
            continue
        # Cheap location pre-check: "India, Pune" or the path's location segment; "3 Locations" needs details.
        hint = f"{p.get('locationsText', '')} {path.split('/')[2] if path.count('/') > 2 else ''}".replace("-", " ")
        if not re.search(r"\d+ Locations", p.get("locationsText", "")) and not _location_ok(hint, "workday"):
            continue
        _pause()
        try:
            d = http.get(f"{base}{path}", headers=JSON).json().get("jobPostingInfo", {})
        except (httpx.HTTPError, ValueError):
            continue
        locs = [d.get("location") or p.get("locationsText", "")] + list(d.get("additionalLocations") or [])
        remote = d.get("remoteType") or ""
        location = "; ".join(x for x in locs if x) + (f" ({remote})" if remote else "")
        if "remote" in remote.lower() and not any("india" in x.lower() for x in locs):
            location += "; Remote"
        jobs.append(Job(
            source="workday",
            external_id=f"{tenant}:{d.get('jobReqId') or path}",
            company=company,
            title=title,
            location=location,
            url=d.get("externalUrl") or f"https://{host}/{site}{path}",
            apply_url=d.get("externalUrl") or f"https://{host}/{site}{path}",
            description=html_to_text(d.get("jobDescription") or ""),
            posted_at=d.get("startDate") or p.get("postedOn"),
        ))
    return jobs


# ---------------------------------------------------------------- SmartRecruiters

def smartrecruiters(company: str, http: httpx.Client, keywords: list[str]) -> list[Job]:
    api = f"https://api.smartrecruiters.com/v1/companies/{company}/postings"
    india_only = cfg("search.location_mode", "any") == "india_remote"
    postings: dict[str, dict] = {}
    for kw in keywords or [""]:
        params = {"q": kw, "limit": 100}
        if india_only:
            params["country"] = "in"
        r = http.get(api, params=params, headers=JSON)
        r.raise_for_status()
        for p in r.json().get("content", []):
            postings.setdefault(p["id"], p)
        _pause()

    jobs = []
    for pid, p in postings.items():
        title = p.get("name", "").strip()
        loc = p.get("location") or {}
        location = loc.get("fullLocation") or ", ".join(x for x in (loc.get("city"), loc.get("country")) if x)
        location = re.sub(r"(,\s*)+", ", ", location).strip(", ")
        if loc.get("remote"):
            location += " (Remote)"
        elif loc.get("hybrid"):
            location += " (Hybrid)"
        if not _title_ok(title) or not _location_ok(location, "smartrecruiters"):
            continue
        _pause()
        try:
            d = http.get(f"{api}/{pid}", headers=JSON).json()
        except (httpx.HTTPError, ValueError):
            continue
        sections = (d.get("jobAd") or {}).get("sections") or {}
        desc = "\n\n".join(
            f"{s.get('title', '')}\n{html_to_text(s.get('text', ''))}".strip()
            for key, s in sections.items() if key != "companyDescription" and s.get("text")
        )
        url = d.get("postingUrl") or f"https://jobs.smartrecruiters.com/{company}/{pid}"
        jobs.append(Job(
            source="smartrecruiters",
            external_id=f"{company}:{pid}",
            company=(d.get("company") or {}).get("name") or company,
            title=title,
            location=location,
            url=url,
            apply_url=d.get("applyUrl") or url,
            description=desc,
            posted_at=p.get("releasedDate"),
        ))
    return jobs


# ---------------------------------------------------------------- Workable

def workable(slug: str, http: httpx.Client, keywords: list[str]) -> list[Job]:
    r = http.get(f"https://apply.workable.com/api/v1/widget/accounts/{slug}", params={"details": "true"},
                 headers=JSON)
    r.raise_for_status()
    data = r.json()
    jobs = []
    for j in data.get("jobs", []):
        title = (j.get("title") or "").strip()
        parts = [j.get("city"), j.get("state"), j.get("country")]
        location = ", ".join(x for x in parts if x) + (" (Remote)" if j.get("telecommuting") else "")
        if not _title_ok(title) or not _location_ok(location, "workable"):
            continue
        url = j.get("url") or j.get("shortlink") or f"https://apply.workable.com/{slug}/"
        jobs.append(Job(
            source="workable",
            external_id=f"{slug}:{j.get('shortcode') or j.get('code') or title}",
            company=data.get("name") or slug.title(),
            title=title,
            location=location,
            url=url,
            apply_url=j.get("application_url") or url,
            description=html_to_text(j.get("description") or ""),
            posted_at=j.get("published_on"),
        ))
    return jobs
