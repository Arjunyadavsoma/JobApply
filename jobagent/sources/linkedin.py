"""LinkedIn job search via the public (logged-out) job pages.

This reads the same pages anyone sees without signing in; it never touches your account.
Requests are throttled - LinkedIn rate-limits aggressively, so keep `pages` small.
"""
from __future__ import annotations

import random
import re
import time
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from jobagent.db import Job
from jobagent.sources.common import html_to_text, log

SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}"


def _pause() -> None:
    time.sleep(random.uniform(1.5, 3.5))


def _search_page(http: httpx.Client, keywords: str, location: str, start: int, max_age_days: int) -> list[dict]:
    params = {"keywords": keywords, "location": location, "start": start,
              "f_TPR": f"r{max_age_days * 86400}"}
    if location.strip().lower() == "remote":
        params.update(location="India", f_WT="2")  # f_WT=2: remote jobs open to people in India
    r = http.get(SEARCH, params=params)
    if r.status_code == 429:
        log.warning("LinkedIn rate limit hit; stopping LinkedIn search for now")
        return []
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    cards = []
    for card in soup.select("div.base-search-card"):
        urn = card.get("data-entity-urn", "")
        m = re.search(r"jobPosting:(\d+)", urn)
        link = card.select_one("a.base-card__full-link")
        if not m or not link:
            continue
        text = lambda sel: (el.get_text(strip=True) if (el := card.select_one(sel)) else None)  # noqa: E731
        posted = card.select_one("time")
        cards.append({
            "id": m.group(1),
            "title": text("h3.base-search-card__title"),
            "company": text("h4.base-search-card__subtitle") or "Unknown",
            "location": text("span.job-search-card__location"),
            "url": link["href"].split("?")[0],
            "posted_at": posted.get("datetime") if posted else None,
        })
    return cards


def _details(http: httpx.Client, job_id: str) -> tuple[str, str | None]:
    """Returns (description, external apply URL if the job isn't Easy Apply)."""
    r = http.get(DETAIL.format(id=job_id))
    if r.status_code != 200:
        return "", None
    soup = BeautifulSoup(r.text, "html.parser")
    desc = soup.select_one("div.show-more-less-html__markup") or soup.select_one("div.description__text")
    apply_url = None
    code = soup.select_one("code#applyUrl")
    if code:
        m = re.search(r'"(https?://[^"]+)"', code.decode_contents())
        if m:
            raw = m.group(1).replace("&amp;", "&")
            target = parse_qs(urlparse(raw).query).get("url")
            apply_url = unquote(target[0]) if target else raw
    return html_to_text(desc.decode_contents()) if desc else "", apply_url


def search(keywords: list[str], locations: list[str], pages: int, max_age_days: int,
           http: httpx.Client) -> list[Job]:
    seen: dict[str, dict] = {}
    for kw in keywords:
        for loc in locations:
            start = 0
            for _ in range(pages):
                cards = _search_page(http, kw, loc, start, max_age_days)
                if not cards:
                    break
                for c in cards:
                    seen.setdefault(c["id"], c)
                start += len(cards)
                _pause()
    log.info(f"linkedin: {len(seen)} unique postings, fetching descriptions...")

    jobs = []
    for c in seen.values():
        if not c["title"]:
            continue
        desc, external = _details(http, c["id"])
        jobs.append(Job(
            source="linkedin",
            external_id=c["id"],
            company=c["company"],
            title=c["title"],
            location=c["location"],
            url=c["url"],
            apply_url=external or c["url"],
            description=desc,
            posted_at=c["posted_at"],
        ))
        _pause()
    return jobs
