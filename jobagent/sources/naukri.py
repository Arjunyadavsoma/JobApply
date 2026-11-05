"""Naukri job search.

Naukri's JSON API is captcha-protected for plain HTTP clients, so we open the normal search
page in a real (visible) browser and read the JSON that the page itself loads.
"""
from __future__ import annotations

import random
import re

from playwright.sync_api import BrowserContext, TimeoutError as PWTimeout

from jobagent.db import Job
from jobagent.sources.common import html_to_text, log


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _search_url(keyword: str, location: str, page: int) -> str:
    loc = location.strip().lower()
    remote = loc == "remote"
    city = "" if remote or loc in ("", "india", "anywhere", "worldwide") else location
    path = f"{_slug(keyword)}-jobs"
    if city:
        path += f"-in-{_slug(city)}"
    if page > 1:
        path += f"-{page}"
    url = f"https://www.naukri.com/{path}?k={keyword}"
    if city:
        url += f"&l={city}"
    if remote:
        url += "&wfhType=2"  # work from home
    return url


def search(keywords: list[str], locations: list[str], pages: int, ctx: BrowserContext) -> list[Job]:
    page = ctx.new_page()
    found: dict[str, dict] = {}
    for kw in keywords:
        for loc in locations:
            for n in range(1, pages + 1):
                try:
                    with page.expect_response(lambda r: "jobapi/v3/search" in r.url, timeout=30000) as resp:
                        page.goto(_search_url(kw, loc, n), wait_until="domcontentloaded")
                    data = resp.value.json()
                except PWTimeout:
                    log.warning(f"naukri: no results loaded for '{kw}' in '{loc}' (page {n}); "
                                "if the window shows a captcha/Access Denied, solve it and rerun")
                    break
                for j in data.get("jobDetails", []):
                    found.setdefault(str(j["jobId"]), j)
                page.wait_for_timeout(random.randint(1500, 3000))
    log.info(f"naukri: {len(found)} unique postings, fetching descriptions...")

    jobs = []
    for jid, j in found.items():
        jd_url = j.get("jdURL", "")
        url = jd_url if jd_url.startswith("http") else f"https://www.naukri.com{jd_url}"
        desc = html_to_text(j.get("jobDescription", ""))
        apply_url = url
        try:
            with page.expect_response(lambda r: f"jobapi/v4/job/{jid}" in r.url, timeout=20000) as resp:
                page.goto(url, wait_until="domcontentloaded")
            details = resp.value.json().get("jobDetails", {})
            desc = html_to_text(details.get("description", "")) or desc
            # "Apply on company site" jobs redirect elsewhere.
            apply_url = details.get("applyRedirectUrl") or url
        except PWTimeout:
            pass
        placeholders = {p["type"]: p["label"] for p in j.get("placeholders", [])}
        skills = j.get("tagsAndSkills", "")
        jobs.append(Job(
            source="naukri",
            external_id=jid,
            company=j.get("companyName", "Unknown"),
            title=j.get("title", "").strip(),
            location=placeholders.get("location"),
            url=url,
            apply_url=apply_url,
            description=(
                f"Experience: {placeholders.get('experience', 'n/a')}\n"
                f"Salary: {placeholders.get('salary', 'n/a')}\nSkills: {skills}\n\n{desc}"
            ),
            posted_at=j.get("footerPlaceholderLabel"),
        ))
        page.wait_for_timeout(random.randint(1000, 2500))
    page.close()
    return jobs
