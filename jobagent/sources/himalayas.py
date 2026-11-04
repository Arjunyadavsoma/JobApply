"""Himalayas public jobs API (https://himalayas.app/jobs/api) - legitimate, key-less.

Verified live in Phase 17.2: the endpoint is a cursor-paginated firehose (20 jobs per
call, filter params are ignored server-side), so we scan a few pages and filter
client-side: tech keywords against title/excerpt/categories + India-or-worldwide
location restrictions. Yield is modest but every listing is real.
"""
from __future__ import annotations

import time

import httpx

from jobagent.db import Job
from jobagent.sources.common import log

API = "https://himalayas.app/jobs/api"
PAGE_SIZE = 20
# Roles whose location restrictions still make them reachable from India.
WORLDWIDE = ("worldwide", "anywhere", "global", "remote")


def _india_ok(restrictions: list) -> bool:
    if not restrictions:
        return True                      # no restriction = worldwide
    text = " ".join(str(r) for r in restrictions).lower()
    if "india" in text:
        return True
    return any(w in text for w in WORLDWIDE)


def _kw_ok(j: dict, keywords: list[str]) -> bool:
    if not keywords:
        return True
    hay = " ".join([j.get("title") or "", j.get("excerpt") or "",
                    " ".join(j.get("categories") or [])]).lower()
    return any(k.lower() in hay for k in keywords)


def search(pages: int, http: httpx.Client, keywords: list[str]) -> list[Job]:
    jobs: list[Job] = []
    seen: set[str] = set()
    cursor = None
    # each API page is only 20 jobs; scan a few pages per configured 'pages' unit
    for _ in range(max(1, pages) * 5):
        params: dict = {"limit": PAGE_SIZE}
        if cursor:
            params["cursor"] = cursor
        try:
            r = http.get(API, params=params)
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            log.warning(f"himalayas: fetch failed ({e})")
            break
        batch = data.get("jobs", [])
        if not batch:
            break
        for j in batch:
            guid = str(j.get("guid") or "")[:120]
            title = (j.get("title") or "").strip()
            if not guid or guid in seen or not title:
                continue
            seen.add(guid)
            if not _kw_ok(j, keywords) or not _india_ok(j.get("locationRestrictions") or []):
                continue
            cur = (j.get("currency") or "").upper()
            period = (j.get("salaryPeriod") or "").upper()
            desc = (j.get("description") or j.get("excerpt") or "")
            if j.get("minSalary") and j.get("maxSalary"):
                desc = (f"Salary: {j['minSalary']} - {j['maxSalary']} {cur} {period}\n\n" + desc)
            jobs.append(Job(
                source="himalayas",
                external_id=guid,
                company=(j.get("companyName") or "Unknown").strip(),
                title=title,
                location="Remote" + (" (India ok)" if j.get("locationRestrictions") else ""),
                url=j.get("applicationLink") or f"https://himalayas.app/jobs/{j.get('companySlug', '')}",
                apply_url=j.get("applicationLink") or "",
                description=desc[:20000],
                posted_at=str(j.get("pubDate") or "")[:10] or None,
            ))
        cursor = data.get("nextCursor")
        if not cursor:
            break
        time.sleep(0.3)
        if len(jobs) >= pages * 50:
            break
    log.info(f"himalayas: {len(jobs)} worldwide/India remote listings (scanned {len(seen)})")
    return jobs
