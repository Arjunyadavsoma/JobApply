"""Remotive public API (https://remotive.com/api/remote-jobs) - legitimate, key-less.

Remote-only jobs across software/data/AI. Searched with the configured keywords; the shared
location filter keeps the remote-anywhere roles and drops country-locked ones.
"""
from __future__ import annotations

import time

import httpx

from jobagent.db import Job
from jobagent.sources.common import log

API = "https://remotive.com/api/remote-jobs"


def search(pages: int, http: httpx.Client, keywords: list[str]) -> list[Job]:
    jobs: list[Job] = []
    seen: set[str] = set()
    for kw in keywords or ["software engineer"]:
        try:
            r = http.get(API, params={"search": kw, "limit": 50})
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            log.warning(f"remotive: search '{kw}' failed ({e})")
            continue
        for j in data.get("jobs", []):
            jid = str(j.get("id") or j.get("url") or "")[:120]
            if not jid or jid in seen:
                continue
            seen.add(jid)
            jobs.append(Job(
                source="remotive",
                external_id=jid,
                company=(j.get("company_name") or "Unknown").strip(),
                title=(j.get("title") or "").strip(),
                location=j.get("candidate_required_location") or "Remote",
                url=j.get("url") or "",
                apply_url=j.get("url") or "",
                description=(j.get("description") or "")[:20000],
                posted_at=(j.get("publication_date") or "")[:10] or None,
            ))
        time.sleep(0.8)
        if len(jobs) >= pages * 50:
            break
    log.info(f"remotive: {len(jobs)} remote listings")
    return jobs
