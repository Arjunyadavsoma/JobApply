"""Arbeitnow public job board API (https://www.arbeitnow.com/api/job-board-api).

Free, documented, no key. Mostly Germany/EU jobs plus international remote roles - useful for
the "international remote" slice of the search. India-based filtering happens via the shared
location filter, so a typical India-first config keeps only its remote/anywhere listings.
"""
from __future__ import annotations

import time

import httpx

from jobagent.db import Job
from jobagent.sources.common import log

API = "https://www.arbeitnow.com/api/job-board-api"


def search(pages: int, http: httpx.Client, keywords: list[str] | None = None) -> list[Job]:
    jobs: list[Job] = []
    url: str | None = API
    for n in range(pages):
        if not url:
            break
        try:
            r = http.get(url)
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            log.warning(f"arbeitnow: page {n + 1} failed ({e})")
            break
        for j in data.get("data", []):
            title = (j.get("title") or "").strip()
            company = (j.get("company_name") or "").strip()
            slug = (j.get("slug") or "").strip()
            if not title or not company or not slug:
                continue
            langs = ", ".join(j.get("tags") or [])[:200]
            remote = "remote" if j.get("remote") else ""
            jobs.append(Job(
                source="arbeitnow",
                external_id=slug,
                company=company,
                title=title,
                location=j.get("location") or "",
                url=j.get("url") or f"https://www.arbeitnow.com/jobs/{slug}",
                apply_url=j.get("url") or f"https://www.arbeitnow.com/jobs/{slug}",
                description=f"Languages: {langs or 'n/a'}\nRemote: {'yes' if j.get('remote') else 'no'}",
                posted_at=str(j.get("created_at") or "")[:10] or None,
            ))
        url = data.get("links", {}).get("next")
        time.sleep(0.6)
    log.info(f"arbeitnow: {len(jobs)} listings")
    return jobs
