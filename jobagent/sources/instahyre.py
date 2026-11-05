"""Instahyre job search via its public job-search API (the same JSON the website loads).

Search results carry title, company, locations and skills; the full description comes from each
job's public page (schema.org JobPosting data), loaded in a hidden Chrome window because the site
blocks plain HTTP clients on those pages. Instahyre is India-only and its API ignores location
filters, so locations are filtered by the shared location filter instead.
"""
from __future__ import annotations

import json
import random
import re
import time

import httpx
from bs4 import BeautifulSoup

from jobagent.db import Job
from jobagent.sources.common import html_to_text, log

API = "https://www.instahyre.com/api/v1/job_search"
PAGE_SIZE = 20


def _details(page, url: str) -> tuple[str, str | None, str | None]:
    """(description, date posted, experience range) from the job's public page."""
    from playwright.sync_api import Error as PWError

    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=30000)
        if not resp or resp.status != 200:
            return "", None, None
        html = page.content()
    except PWError:
        return "", None, None
    soup = BeautifulSoup(html, "html.parser")
    desc, posted = "", None
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            desc = html_to_text(data.get("description") or "")
            posted = data.get("datePosted")
            break
    meta = soup.find("meta", attrs={"name": "description"})
    m = re.search(r"(\d+\s*-\s*\d+|\d+\+?)\s*years", meta.get("content", "") if meta else "")
    return desc, posted, (m.group(1).replace(" ", "") + " years") if m else None


def _to_job(page, o: dict) -> tuple[Job, bool]:
    """Build a Job from a search result; returns (job, whether the full description was found)."""
    url = o.get("public_url") or f"https://www.instahyre.com/job-{o['id']}/"
    desc, posted, exp = _details(page, url)
    skills_line = ", ".join(o.get("keywords") or [])
    job = Job(
        source="instahyre",
        external_id=str(o["id"]),
        company=(o.get("employer") or {}).get("company_name") or "Unknown",
        title=(o.get("title") or "").strip(),
        location=(o.get("locations") or "").replace(",", ", "),
        url=url,
        apply_url=url,
        description=f"Experience: {exp or 'n/a'}\nSkills: {skills_line}\n\n{desc}".strip(),
        posted_at=posted,
    )
    return job, bool(desc)


def search(skills: list[str], job_functions: list[int], years: int | None, pages: int,
           http: httpx.Client) -> list[Job]:
    found: dict[int, dict] = {}
    for n in range(pages):
        params: list[tuple[str, str]] = [("company_size", "0"), ("job_type", "0"), ("isLandingPage", "true"),
                                         ("limit", str(PAGE_SIZE)), ("offset", str(n * PAGE_SIZE))]
        params += [("skills", s) for s in skills]
        params += [("job_functions", str(f)) for f in job_functions]
        if years is not None:
            params.append(("years", str(years)))
        try:
            r = http.get(API, params=params)
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            log.warning(f"instahyre: search failed ({e})")
            break
        for o in data.get("objects", []):
            found.setdefault(o["id"], o)
        if n == 0:
            log.info(f"instahyre: {data.get('meta', {}).get('total_count', '?')} matching jobs on the site")
        if not data.get("meta", {}).get("next"):
            break
        time.sleep(random.uniform(1.0, 2.0))
    log.info(f"instahyre: {len(found)} postings, fetching descriptions...")
    if not found:
        return []

    from jobagent.browser import headless_page

    jobs, missing = [], 0
    with headless_page() as page:
        for o in found.values():
            job, ok = _to_job(page, o)
            jobs.append(job)
            missing += not ok
            time.sleep(random.uniform(0.5, 1.2))
    if missing:
        log.warning(f"instahyre: {missing} job page(s) couldn't be read - they'll be scored on title/skills only")
    return jobs
