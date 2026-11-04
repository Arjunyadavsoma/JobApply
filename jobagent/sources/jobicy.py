"""Jobicy public API v2 (https://jobicy.com/api/v2/remote-jobs) - legitimate, key-less.

Remote jobs for developers with a `tag` search parameter, geo labels (Worldwide /
region) and salary data. Verified live in Phase 17.2. Credit: Jobicy asks API users to
link back to jobicy.com - the job URL we store IS their canonical listing page.
"""
from __future__ import annotations

import time

import httpx

from jobagent.db import Job
from jobagent.sources.common import log

API = "https://jobicy.com/api/v2/remote-jobs"


def search(pages: int, http: httpx.Client, keywords: list[str]) -> list[Job]:
    jobs: list[Job] = []
    seen: set[str] = set()
    for kw in keywords or ["software engineer"]:
        try:
            r = http.get(API, params={"industry": "dev", "count": 50, "tag": kw})
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            log.warning(f"jobicy: search '{kw}' failed ({e})")
            continue
        for j in data.get("jobs", []):
            jid = str(j.get("id") or j.get("url") or "")[:120]
            if not jid or jid in seen:
                continue
            seen.add(jid)
            geo = (j.get("jobGeo") or "Remote").strip()
            # Worldwide/Anywhere roles are reachable from India; country-locked ones are not.
            if geo.lower() not in ("worldwide", "anywhere", "remote", "global") and "india" not in geo.lower():
                continue
            cur = (j.get("salaryCurrency") or "").upper()
            period = (j.get("salaryPeriod") or "").lower()
            desc = (j.get("jobDescription") or j.get("jobExcerpt") or "")
            if j.get("salaryMin") and j.get("salaryMax"):
                desc = (f"Salary: {j['salaryMin']} - {j['salaryMax']} {cur} {period}\n\n" + desc)
            jobs.append(Job(
                source="jobicy",
                external_id=jid,
                company=(j.get("companyName") or "Unknown").strip(),
                title=(j.get("jobTitle") or "").strip(),
                location=f"Remote ({geo})" if geo.lower() not in ("remote",) else "Remote",
                url=j.get("url") or "",
                apply_url=j.get("url") or "",
                description=desc[:20000],
                posted_at=str(j.get("pubDate") or "")[:10] or None,
            ))
        time.sleep(0.8)
    log.info(f"jobicy: {len(jobs)} remote listings")
    return jobs
