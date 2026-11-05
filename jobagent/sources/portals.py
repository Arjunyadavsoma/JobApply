"""Company career portals: paste any careers URL and the agent searches that company's own site.

Detection (cached in data/portal_cache.json):
  1. Known hosts   - Amazon, Google, Apple, Oracle Recruiting Cloud, Workday, Greenhouse, Lever, Ashby,
                     SmartRecruiters, Workable
  2. Eightfold     - probed via its public search API (Microsoft, Qualcomm, ...)
  3. Embedded ATS  - the careers page links to a Greenhouse/Lever/Workday/... board -> use that board
  4. Anything else - generic: a hidden Chrome window searches the page for your keywords and Claude
                     picks the relevant job links; each job page is then read (JSON-LD when present).

Every portal is searched with your keywords; title/location filters apply before details are fetched.
"""
from __future__ import annotations

import json
import random
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote_plus, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from pydantic import BaseModel, Field

from jobagent.config import DATA_DIR, cfg
from jobagent.db import Job
from jobagent.sources import ats
from jobagent.sources.ats_more import _location_ok, _title_ok
from jobagent.sources.common import html_to_text, log

CACHE = DATA_DIR / "portal_cache.json"
JSON = {"Accept": "application/json"}


def _pause(lo: float = 0.4, hi: float = 1.0) -> None:
    time.sleep(random.uniform(lo, hi))


def _india() -> bool:
    return cfg("search.location_mode", "any") == "india_remote"


def _pages() -> int:
    return int(cfg("sources.portal_pages", 2))


def _job(kind: str, company: str, ext_id: str, title: str, location: str, url: str, desc: str,
         posted: str | None = None, apply_url: str | None = None) -> Job:
    return Job(source=kind, external_id=f"{company}:{ext_id}", company=company, title=title.strip(),
               location=location, url=url, apply_url=apply_url or url, description=desc, posted_at=posted)


# ================================================================ Amazon (amazon.jobs)

def amazon(company: str, url: str, http: httpx.Client, keywords: list[str]) -> list[Job]:
    seen: dict[str, dict] = {}
    for kw in keywords:
        for n in range(_pages()):
            params = {"base_query": kw, "result_limit": 50, "offset": n * 50, "sort": "recent"}
            if _india():
                params["normalized_country_code[]"] = "IND"
            r = http.get("https://www.amazon.jobs/en/search.json", params=params, headers=JSON)
            r.raise_for_status()
            batch = r.json().get("jobs", [])
            for j in batch:
                seen.setdefault(str(j.get("id_icims") or j.get("id")), j)
            if len(batch) < 50:
                break
            _pause()
    out = []
    for jid, j in seen.items():
        loc = j.get("normalized_location") or j.get("location") or ""
        if not _title_ok(j.get("title", "")) or not _location_ok(loc, "amazon"):
            continue
        desc = "\n\n".join(filter(None, [
            html_to_text(j.get("description") or ""),
            "Basic qualifications:\n" + html_to_text(j.get("basic_qualifications") or ""),
            "Preferred qualifications:\n" + html_to_text(j.get("preferred_qualifications") or ""),
        ]))
        link = "https://www.amazon.jobs" + (j.get("job_path") or f"/en/jobs/{jid}")
        out.append(_job("amazon", company, jid, j.get("title", ""), loc, link, desc, j.get("posted_date")))
    return out


# ================================================================ Google Careers

def google(company: str, url: str, http: httpx.Client, keywords: list[str]) -> list[Job]:
    base = "https://www.google.com/about/careers/applications/jobs/results"
    seen: dict[str, list] = {}
    for kw in keywords:
        for n in range(1, _pages() + 1):
            params = {"q": kw, "page": n}
            if _india():
                params["location"] = "India"
            r = http.get(base, params=params)
            r.raise_for_status()
            m = re.search(r"AF_initDataCallback\(\{key: 'ds:1'.*?data:(\[.*?\]), sideChannel", r.text, re.S)
            batch = (json.loads(m.group(1))[0] or []) if m else []
            for j in batch:
                seen.setdefault(str(j[0]), j)
            if len(batch) < 20:
                break
            _pause()
    out = []
    for jid, j in seen.items():
        title = j[1] or ""
        loc = "; ".join(l[0] for l in (j[9] or []) if l)
        if not _title_ok(title) or not _location_ok(loc, "google"):
            continue
        part = lambda i: html_to_text(j[i][1]) if len(j) > i and isinstance(j[i], list) and len(j[i]) > 1 and j[i][1] else ""  # noqa: E731
        desc = "\n\n".join(filter(None, [part(10), "Responsibilities:\n" + part(3), "Qualifications:\n" + part(4),
                                         part(19)]))
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        posted = datetime.fromtimestamp(j[12][0], timezone.utc).date().isoformat() if len(j) > 12 and j[12] else None
        out.append(_job("google", company, jid, title, loc, f"{base}/{jid}-{slug}", desc, posted,
                        apply_url=j[2] if isinstance(j[2], str) else None))
    return out


# ================================================================ Apple Jobs

def _apple_hydration(html: str) -> dict:
    m = re.search(r'window\.__staticRouterHydrationData\s*=\s*JSON\.parse\("(.*?)"\);', html, re.S)
    return json.loads(json.loads('"' + m.group(1) + '"')).get("loaderData", {}) if m else {}


def apple(company: str, url: str, http: httpx.Client, keywords: list[str]) -> list[Job]:
    seen: dict[str, dict] = {}
    for kw in keywords:
        for n in range(1, _pages() + 1):
            params = {"search": kw, "page": n}
            if _india():
                params["location"] = "india-INDC"
            r = http.get("https://jobs.apple.com/en-in/search", params=params)
            r.raise_for_status()
            batch = (_apple_hydration(r.text).get("search") or {}).get("searchResults") or []
            for j in batch:
                seen.setdefault(str(j.get("positionId")), j)
            if len(batch) < 20:
                break
            _pause()
    out = []
    for pid, j in seen.items():
        title = j.get("postingTitle", "")
        loc = "; ".join(filter(None, (f"{l.get('name', '')}, {l.get('countryName', '')}".strip(", ")
                                      for l in j.get("locations") or [])))
        if not _title_ok(title) or not _location_ok(loc, "apple"):
            continue
        link = f"https://jobs.apple.com/en-in/details/{pid}/{j.get('transformedPostingTitle', '')}"
        _pause()
        try:
            d = (_apple_hydration(http.get(link).text).get("jobDetails") or {}).get("jobsData") or {}
        except httpx.HTTPError:
            d = {}
        desc = "\n\n".join(f"{label}:\n{html_to_text(d[k])}" for k, label in (
            ("jobSummary", "Summary"), ("description", "Description"),
            ("minimumQualifications", "Minimum qualifications"),
            ("preferredQualifications", "Preferred qualifications")) if d.get(k))
        out.append(_job("apple", company, pid, title, loc, link, desc or j.get("jobSummary", ""),
                        j.get("postDateInGMT") or j.get("postingDate")))
    return out


# ================================================================ Eightfold (Microsoft, Qualcomm, ...)

def _registered_domain(host: str) -> str:
    parts = host.split(".")
    return ".".join(parts[-3:]) if len(parts) > 2 and parts[-2] in ("co", "com", "net", "org") else ".".join(parts[-2:])


def eightfold(company: str, url: str, http: httpx.Client, keywords: list[str], domain: str | None = None) -> list[Job]:
    host = urlparse(url).netloc
    domain = domain or _registered_domain(host)
    api = f"https://{host}/api/pcsx"
    seen: dict[str, dict] = {}
    for kw in keywords:
        for n in range(_pages()):
            params = {"domain": domain, "query": kw, "start": n * 10}
            if _india():
                params["location"] = "India"
            r = http.get(f"{api}/search", params=params, headers=JSON)
            r.raise_for_status()
            batch = (r.json().get("data") or {}).get("positions") or []
            for p in batch:
                seen.setdefault(str(p["id"]), p)
            if len(batch) < 10:
                break
            _pause()
    out = []
    for pid, p in seen.items():
        loc = "; ".join(p.get("locations") or [])
        if p.get("workLocationOption") and "remote" in str(p["workLocationOption"]).lower():
            loc += " (Remote)"
        if not _title_ok(p.get("name", "")) or not _location_ok(loc, "eightfold"):
            continue
        _pause()
        try:
            d = http.get(f"{api}/position_details", params={"position_id": pid, "domain": domain, "hl": "en"},
                         headers=JSON).json().get("data") or {}
        except (httpx.HTTPError, ValueError):
            d = {}
        link = d.get("publicUrl") or f"https://{host}{p.get('positionUrl', '')}"
        posted = datetime.fromtimestamp(p["postedTs"], timezone.utc).date().isoformat() if p.get("postedTs") else None
        out.append(_job("eightfold", company, pid, p.get("name", ""), loc, link,
                        html_to_text(d.get("jobDescription") or ""), posted))
    return out


def _is_eightfold(url: str, http: httpx.Client) -> str | None:
    """Return the Eightfold `domain` if this host serves the Eightfold search API."""
    host = urlparse(url).netloc
    for domain in dict.fromkeys([_registered_domain(host), host]):
        try:
            r = http.get(f"https://{host}/api/pcsx/search", params={"domain": domain, "query": "", "start": 0},
                         headers=JSON, timeout=15)
            if r.status_code == 200 and "positions" in (r.json().get("data") or {}):
                return domain
        except (httpx.HTTPError, ValueError):
            continue
    return None


# ================================================================ Oracle Recruiting Cloud (JPMorgan, ...)

def oracle(company: str, url: str, http: httpx.Client, keywords: list[str]) -> list[Job]:
    u = urlparse(url)
    site = (re.search(r"/sites/([A-Za-z0-9_]+)", u.path) or re.search(r"siteNumber=([A-Za-z0-9_]+)", url))
    if not site:
        raise ValueError("expected an Oracle careers URL containing /sites/<SITE_NUMBER>")
    site = site.group(1)
    api = f"https://{u.netloc}/hcmRestApi/resources/latest"
    seen: dict[str, dict] = {}
    for kw in keywords:
        for n in range(_pages() * 2):  # 25 per page
            finder = f"findReqs;siteNumber={site},keyword={kw},limit=25,offset={n * 25},sortBy=POSTING_DATES_DESC"
            r = http.get(f"{api}/recruitingCEJobRequisitions", headers=JSON,
                         params={"onlyData": "true", "expand": "requisitionList.secondaryLocations", "finder": finder})
            r.raise_for_status()
            items = r.json().get("items") or [{}]
            batch = items[0].get("requisitionList") or []
            for q in batch:
                seen.setdefault(str(q["Id"]), q)
            if len(batch) < 25:
                break
            _pause()
    out = []
    for rid, q in seen.items():
        locs = [q.get("PrimaryLocation") or ""] + [s.get("Name", "") for s in q.get("secondaryLocations") or []]
        loc = "; ".join(x for x in locs if x) + (" (Remote)" if "REMOTE" in str(q.get("WorkplaceTypeCode", "")).upper() else "")
        if not _title_ok(q.get("Title", "")) or not _location_ok(loc, "oracle"):
            continue
        _pause()
        try:
            d = (http.get(f"{api}/recruitingCEJobRequisitionDetails", headers=JSON,
                          params={"expand": "all", "onlyData": "true",
                                  "finder": f'ById;Id="{rid}",siteNumber={site}'}).json().get("items") or [{}])[0]
        except (httpx.HTTPError, ValueError):
            d = {}
        desc = "\n\n".join(html_to_text(d.get(k) or "") for k in (
            "ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr") if d.get(k))
        link = f"https://{u.netloc}/hcmUI/CandidateExperience/en/sites/{site}/job/{rid}"
        out.append(_job("oracle", company, rid, q.get("Title", ""), loc, link, desc, q.get("PostedDate")))
    return out


# ================================================================ MyNextHire (Swiggy and other Indian companies)

def mynexthire(company: str, tenant: str, http: httpx.Client, keywords: list[str]) -> list[Job]:
    """Lists every open role (with description) in one request; filters run locally."""
    r = http.post(f"https://{tenant}.mynexthire.com/employer/careers/reqlist/get",
                  json={"source": "careers", "code": "", "filterByBuId": -1}, headers=JSON)
    r.raise_for_status()
    out = []
    for q in r.json().get("reqDetailsBOList") or []:
        title = q.get("reqTitle") or ""
        locs = [l.get("name") or l.get("location") or "" for l in q.get("locationList") or [] if isinstance(l, dict)]
        loc = "; ".join(x for x in [q.get("locationAddress") or q.get("location") or ""] + locs if x)
        if not _title_ok(title) or not _location_ok(loc, "mynexthire"):
            continue
        header = []
        if q.get("expMin") is not None and q.get("expMax"):
            header.append(f"Experience: {q['expMin']:g}-{q['expMax']:g} years")
        skills = ", ".join(s.get("name", "") for s in (q.get("mandatorySkillList") or []) if isinstance(s, dict))
        if skills:
            header.append(f"Skills: {skills}")
        desc = "\n".join(header + ["", q.get("jdDisplay") or ""]).strip()
        link = f"https://{tenant}.mynexthire.com/employer/jobs?src=careers&reqId={q['reqId']}"
        out.append(_job("mynexthire", company, str(q["reqId"]), title, loc, link, desc,
                        (q.get("approvedOn") or "")[:10] or None))
    return out


# ================================================================ SAP SuccessFactors (Standard Chartered, SAP, ...)

def _sf_locale(html: str) -> str:
    m = re.search(r'"locale"\s*:\s*"([a-z]{2}_[A-Z]{2})"', html) or re.search(r'<html[^>]*lang="([a-z]{2})[-_]([A-Z]{2})"', html)
    if not m:
        return "en_US"
    return m.group(1) if m.lastindex == 1 else f"{m.group(1)}_{m.group(2)}"


def _sf_description(http: httpx.Client, url: str) -> str:
    try:
        soup = BeautifulSoup(http.get(url).text, "html.parser")
    except httpx.HTTPError:
        return ""
    el = soup.select_one("[itemprop=description], .jobdescription, #job-description, .job-description")
    return el.get_text("\n", strip=True) if el else ""


def successfactors(company: str, url: str, http: httpx.Client, keywords: list[str]) -> list[Job]:
    """SuccessFactors career sites: the newer search API (needs the site's session + CSRF token), or the
    older server-rendered result list as a fallback."""
    u = urlparse(url)
    base = f"{u.scheme or 'https'}://{u.netloc}"
    location = "India" if _india() else ""
    page = http.get(f"{base}/search/", params={"q": "", "locationsearch": location})
    token = re.search(r'CSRFToken\s*[=:]\s*["\']([0-9a-f-]{20,})["\']', page.text)
    locale = _sf_locale(page.text)
    found: dict[str, dict] = {}
    for kw in keywords:
        for n in range(_pages()):
            batch: list[dict] = []
            if token:
                r = http.post(f"{base}/services/recruiting/v1/jobs", headers={"x-csrf-token": token.group(1)},
                              json={"locale": locale, "pageNumber": n, "sortBy": "", "keywords": kw,
                                    "location": location, "facetFilters": {}, "brand": "", "skills": [],
                                    "categoryId": 0, "alertId": "", "rcmCandidateId": ""})
                if r.status_code == 200:
                    for res in r.json().get("jobSearchResult") or []:
                        j = res.get("response") or {}
                        batch.append({"id": str(j.get("id")), "title": j.get("unifiedStandardTitle") or "",
                                      "location": "; ".join(x.strip() for x in j.get("jobLocationShort") or []),
                                      "url": f"{base}/job/{j.get('urlTitle')}/{j.get('id')}-{locale}",
                                      "posted": j.get("unifiedStandardStart")})
            if not token or not batch and n == 0:
                # Older SuccessFactors sites render the result table on the server.
                soup = BeautifulSoup(http.get(f"{base}/search/", params={
                    "q": kw, "locationsearch": location, "startrow": n * 25}).text, "html.parser")
                for a in soup.select("a.jobTitle-link"):
                    row = a.find_parent("tr") or a.find_parent("li")
                    loc = row.select_one(".jobLocation") if row else None
                    href = urljoin(base, a.get("href", ""))
                    batch.append({"id": href, "title": a.get_text(strip=True),
                                  "location": loc.get_text(" ", strip=True) if loc else "", "url": href,
                                  "posted": None})
            for j in batch:
                found.setdefault(j["id"], j)
            if len(batch) < 10:
                break
            _pause()
    out = []
    for jid, j in found.items():
        if not _title_ok(j["title"]) or not _location_ok(j["location"], "successfactors"):
            continue
        _pause()
        out.append(_job("successfactors", company, jid, j["title"], j["location"], j["url"],
                        _sf_description(http, j["url"]), j["posted"]))
    return out


def _is_successfactors(html: str) -> bool:
    low = html.lower()
    return "successfactors" in low and ("rmk" in low or "jobtitle-link" in low or "/services/recruiting" in low)


# ================================================================ Generic careers page (hidden Chrome + Claude)

class PortalLink(BaseModel):
    title: str
    url: str
    location: str = Field(description="Location shown next to the job, or empty")


class PortalLinks(BaseModel):
    jobs: list[PortalLink] = Field(description="Individual job postings relevant to the keywords")


SEARCH_BOX = ("input[type=search], input[placeholder*='search' i], input[placeholder*='keyword' i], "
              "input[aria-label*='search' i], input[name*='keyword' i], input[name*='query' i], input[name='q']")
LINKS_JS = """() => [...document.querySelectorAll('a[href]')]
  .map(a => ({text: (a.innerText || a.getAttribute('aria-label') || '').replace(/\\s+/g, ' ').trim().slice(0, 160),
              href: a.href}))
  .filter(l => l.text.length > 3 && l.href.startsWith('http'))"""


LISTING = re.compile(r"(all|open|current|view|see|browse|search|explore)\b.{0,12}(jobs|roles|openings|positions|opportunit)"
                     r"|^(jobs|careers|openings|opportunities|open roles|join us|technology|engineering|explore)$", re.I)


def _key(href: str) -> str:
    # Keep "#/route" fragments (single-page apps route with them); drop plain "#section" anchors.
    return href if "#/" in href or "#!/" in href else href.split("#")[0]


def _gather(page, links: dict[str, dict]) -> None:
    for link in page.evaluate(LINKS_JS):
        links.setdefault(_key(link["href"]), link)


def _open(page, url: str, settle_ms: int = 1500) -> None:
    """Load a page without waiting for full network silence (some sites never go idle)."""
    from playwright.sync_api import TimeoutError as PWTimeout

    page.goto(url, wait_until="domcontentloaded", timeout=45000)
    try:
        page.wait_for_load_state("networkidle", timeout=8000)
    except PWTimeout:
        pass
    page.wait_for_timeout(settle_ms)


def _collect_links(page, url: str, keywords: list[str], max_listing_pages: int = 5) -> list[dict]:
    """Search the careers site for each keyword (URL template or on-page search box), and follow a few
    'see all jobs' / category links when the landing page itself doesn't list jobs."""
    from playwright.sync_api import Error as PWError

    links: dict[str, dict] = {}
    searched = False
    for kw in keywords:
        try:
            if "{keyword}" in url:
                _open(page, url.replace("{keyword}", quote_plus(kw)))
                searched = True
            else:
                _open(page, url)
                box = page.locator(SEARCH_BOX).first
                if box.count() and box.is_visible():
                    box.fill(kw)
                    box.press("Enter")
                    page.wait_for_timeout(4000)
                    searched = True
            page.wait_for_timeout(1500)
            _gather(page, links)
        except PWError as e:
            log.warning(f"portal {url}: {str(e).splitlines()[0][:120]}")
        if not searched:
            break  # no search on this site: one pass, then follow listing links below

    if not searched:
        site = urlparse(url).netloc.split(".")[-2]
        listing = [l for l in links.values() if site in urlparse(l["href"]).netloc and
                   (LISTING.search(l["text"]) or re.search(r"/(jobs|openings|careers|positions)(\b|\?|$)",
                                                           l["href"], re.I))]
        for l in listing[:max_listing_pages]:
            try:
                _open(page, l["href"], 2000)
                page.wait_for_timeout(2000)
                _gather(page, links)
            except PWError:
                continue
    return list(links.values())


def _infer_location(text: str) -> str:
    """Pull an India city / 'Remote' out of a job page when it has no structured location."""
    from jobagent.sources.location import INDIA, REMOTE_WORDS, _has

    low = text.lower()
    cities = [c for c in INDIA if c not in ("india", "ncr") and len(c) > 3 and _has(low, [c])]
    if cities:
        return ", ".join(dict.fromkeys(c.title() for c in cities[:3])) + ", India"
    if _has(low, ["india"]):
        return "India"
    if _has(low, REMOTE_WORDS):
        return "Remote"
    return ""


def _job_page(page, url: str) -> tuple[str, str, str | None]:
    """(description, location, date posted) from a job page - JSON-LD JobPosting when present."""
    from playwright.sync_api import Error as PWError

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1500)
        html = page.content()
    except PWError:
        return "", "", None
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except json.JSONDecodeError:
            continue
        for d in data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []:
            if isinstance(d, dict) and d.get("@type") == "JobPosting":
                places = d.get("jobLocation") or []
                places = places if isinstance(places, list) else [places]
                loc = "; ".join(", ".join(filter(None, [(p.get("address") or {}).get(k) for k in (
                    "addressLocality", "addressRegion", "addressCountry")])) for p in places if isinstance(p, dict))
                if d.get("jobLocationType") == "TELECOMMUTE":
                    loc += " (Remote)"
                return html_to_text(d.get("description") or ""), loc, d.get("datePosted")
    main = soup.select_one("main, article, [role=main]") or soup.body
    return (main.get_text("\n", strip=True) if main else ""), "", None


def generic(company: str, url: str, http: httpx.Client, keywords: list[str]) -> list[Job]:
    from jobagent.browser import headless_page
    from jobagent.llm import LLMError, structured

    host = urlparse(url.replace("{keyword}", "x")).netloc
    with headless_page() as page:
        links = _collect_links(page, url, keywords)
        # Keep plausible links (same site or a known job host), then let Claude pick the job postings.
        cand = [l for l in links if host.split(".")[-2] in urlparse(l["href"]).netloc or
                re.search(r"job|career|position|opening|requisition|vacanc", l["href"], re.I)][:400]
        if not cand:
            log.warning(f"portal {company}: no links found on {url}")
            return []
        try:
            picked = structured(
                PortalLinks,
                system="You read the links on a company's careers page and return only the ones that are "
                       "individual job postings (not categories, filters, blog posts, or navigation). "
                       "Return every job posting that could fit the candidate's target roles; skip clearly "
                       "unrelated roles (sales, HR, legal, ...). Use the link text as the title.",
                content=f"Company: {company}\nTarget roles: {', '.join(keywords)}\n"
                        f"Preferred locations: {', '.join(cfg('search.locations', []) or [])}\n\n"
                        f"<links>\n{json.dumps(cand, indent=0)}\n</links>",
                effort="low",
            )
        except LLMError as e:
            log.warning(f"portal {company}: {e}")
            return []
        out = []
        for pl in picked.jobs:
            link = urljoin(url, pl.url)
            if not _title_ok(pl.title):
                continue
            desc, loc, posted = _job_page(page, link)
            loc = loc or pl.location or _infer_location(f"{pl.title}\n{desc}")
            # Without a confirmed location we can't tell an India job from a US one: skip it in India mode.
            if (not loc and _india()) or (loc and not _location_ok(loc, "portal")):
                continue
            out.append(_job("portal", company, link, pl.title, loc or "", link, desc, posted))
            _pause(0.3, 0.8)
    return out


# ================================================================ detection + dispatch

EMBEDDED = [  # careers pages that embed a known job board
    (r"(?:boards|job-boards)\.eu\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)", "greenhouse_eu"),
    (r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)", "greenhouse"),
    (r"jobs\.lever\.co/([A-Za-z0-9_-]+)", "lever"),
    (r"jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+)", "ashby"),
    (r"(https://[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?[A-Za-z0-9_-]+)", "workday"),
    (r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)", "smartrecruiters"),
    (r"apply\.workable\.com/([A-Za-z0-9_-]+)", "workable"),
    (r"https?://([a-z0-9-]+)\.mynexthire\.com", "mynexthire"),
]


def _load_cache() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def detect(url: str, http: httpx.Client) -> dict:
    """{'kind': ..., 'arg': ...} describing how to search this careers URL."""
    host = urlparse(url if "//" in url else f"https://{url}").netloc.lower()
    known = [("amazon.jobs", "amazon"), ("jobs.apple.com", "apple"), ("oraclecloud.com", "oracle")]
    for needle, kind in known:
        if host.endswith(needle):
            return {"kind": kind, "arg": url}
    if host.endswith("google.com") and "careers" in url:
        return {"kind": "google", "arg": url}
    for pattern, kind in EMBEDDED:
        m = re.search(pattern, url)
        if m:
            return {"kind": kind, "arg": m.group(1)}
    domain = _is_eightfold(url, http)
    if domain:
        return {"kind": "eightfold", "arg": url, "domain": domain}
    try:  # does the careers page embed a known job board?
        found = _embedded_board(http.get(url.replace("{keyword}", ""), timeout=20).text)
        if found:
            return {**found, "arg": found["arg"] or url}
    except httpx.HTTPError:
        pass
    found = _embedded_board_rendered(url.replace("{keyword}", ""))
    if found:
        return {**found, "arg": found["arg"] or url}
    return {"kind": "generic", "arg": url}


def _embedded_board(text: str) -> dict | None:
    for pattern, kind in EMBEDDED:
        for m in re.finditer(pattern, text):
            if m.group(1).lower() not in ("embed", "js", "static", "v1", "api"):
                if kind == "greenhouse_eu":
                    return {"kind": "greenhouse", "arg": f"eu:{m.group(1)}"}
                return {"kind": kind, "arg": m.group(1)}
    if _is_successfactors(text):
        return {"kind": "successfactors", "arg": None}
    return None


def _embedded_board_rendered(url: str) -> dict | None:
    """Many careers pages load their job board with JavaScript: open the page once in a hidden browser and
    look at the rendered HTML, iframes and network requests for a known board."""
    from playwright.sync_api import Error as PWError

    from jobagent.browser import headless_page

    requests: list[str] = []
    try:
        with headless_page() as page:
            page.on("request", lambda r: requests.append(r.url))
            _open(page, url, 2500)
            text = page.content() + "\n" + "\n".join(f.url for f in page.frames) + "\n" + "\n".join(requests)
    except PWError:
        return None
    return _embedded_board(text)


ADAPTERS = {"amazon": amazon, "google": google, "apple": apple, "oracle": oracle, "mynexthire": mynexthire,
            "successfactors": successfactors,
            "generic": generic}


def search_portal(entry: str, http: httpx.Client, keywords: list[str]) -> tuple[str, list[Job]]:
    """`entry`: "Name | careers URL" (or just the URL). Returns (how it was searched, jobs)."""
    name, _, url = entry.rpartition("|")
    url = url.strip()
    company = name.strip() or urlparse(url if "//" in url else f"https://{url}").netloc.split(".")[-2].title()
    cache = _load_cache()
    how = cache.get(url)
    if not how:
        how = detect(url, http)
        cache[url] = how
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cache, indent=1), encoding="utf-8")
    kind, arg = how["kind"], how["arg"]
    if kind == "eightfold":
        jobs = eightfold(company, arg, http, keywords, how.get("domain"))
    elif kind in ADAPTERS:
        jobs = ADAPTERS[kind](company, arg, http, keywords)
    elif kind in ats.KEYWORD_FETCHERS:
        jobs = ats.KEYWORD_FETCHERS[kind](f"{company} | {arg}" if kind == "workday" else arg, http, keywords)
    else:
        jobs = ats.FETCHERS[kind](arg, http)
        for j in jobs:
            j.company = company
    return kind, jobs


def fetch_portals(entries: list[str], http: httpx.Client, keywords: list[str]) -> list[Job]:
    out: list[Job] = []
    for entry in entries or []:
        try:
            kind, jobs = search_portal(entry, http, keywords or [""])
            log.info(f"portal {entry.split('|')[0].strip()} ({kind}): {len(jobs)} matching roles")
            out.extend(jobs)
        except httpx.HTTPStatusError as e:
            log.warning(f"portal {entry}: HTTP {e.response.status_code}")
        except (httpx.HTTPError, ValueError, KeyError, IndexError) as e:
            log.warning(f"portal {entry}: {type(e).__name__}: {e}")
    return out
