"""Phase 17.2 — external-link discovery registry.

Every India job platform that has NO legitimate programmatic access for this tool
(no public API, robots.txt forbids their API/search, ToS prohibits scraping, or their
bot protection blocks datacenter clients) is registered HERE instead of being faked.

What we do: build precise search deep-links using the user's own keywords, so the
dashboard can open "search Naukri-like portals" in one click. What we never do:
scrape those sites, pretend to have listings from them, or bypass any protection.

Verdicts were established by live probes (robots.txt, API responses, headless
Chromium) on 2026-11-05 - see PHASE_17_REPORT.md for the full evidence table.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote_plus


@dataclass(frozen=True)
class ExternalSource:
    key: str
    name: str
    priority: int                      # 1 = India-first core, 2 = secondary, 3 = MNC/campus
    reason: str                        # why there is no adapter (evidence-based)
    kind: str = "portal"               # portal | campus | ats
    search: str | None = None          # {}-formatted deep-link template using `kw`
    fresher_search: str | None = None  # link tuned for fresher/entry-level browsing


def _q(kw: str) -> str:
    return quote_plus(kw)


SOURCES: list[ExternalSource] = [
    # ---- Priority 1 (India core) ----
    ExternalSource(
        "naukri", "Naukri.com", 1,
        "Integrated via the real-browser adapter (jobagent/sources/naukri.py); the public search "
        "page is used, never their captcha-protected JSON API directly.",
        search="https://www.naukri.com/{kw}-jobs"),
    ExternalSource(
        "internshala", "Internshala", 1,
        "robots.txt disallows /api/, /internship/details/ and /job/details/; ToS prohibits "
        "scraping. Internships surface via the other adapters; use the link to apply directly.",
        kind="portal",
        search="https://internshala.com/internships/keywords-{kw}",
        fresher_search="https://internshala.com/internships/"),
    ExternalSource(
        "foundit", "Foundit (Monster India)", 1,
        "robots.txt disallows /middleware/ (their JSON API) and job pages return HTTP 403 to "
        "non-browser clients (Akamai). Their XML job sitemaps are public but detail pages "
        "are blocked from datacenter IPs.",
        search="https://www.foundit.in/srp/results?query={kw}&locations=Hyderabad"),
    ExternalSource(
        "linkedin", "LinkedIn Jobs", 1,
        "Integrated via the logged-out guest search API (jobagent/sources/linkedin.py) with "
        "low page counts; log in for the full experience.",
        search="https://www.linkedin.com/jobs/search/?keywords={kw}&location=India"),
    ExternalSource(
        "freshersworld", "Freshersworld", 1,
        "Whole site returns HTTP 403 to this environment (bot protection); no public API.",
        search="https://www.freshersworld.com/jobs/job-search/{kw}-jobs"),
    ExternalSource(
        "cutshort", "Cutshort", 1,
        "robots.txt disallows /view/j/ (job views) and /apply/; no public jobs API found; "
        "job data loads behind their SPA auth.",
        search="https://cutshort.io/jobs?search={kw}"),
    ExternalSource(
        "hirist", "Hirist (Hirist.tech)", 1,
        "Search pages are an SPA that returned HTTP 403 to headless Chromium from this "
        "datacenter IP; no public API. (Hirist and iimjobs share one platform.)",
        search="https://www.hirist.tech/jobs?keywords={kw}"),
    ExternalSource(
        "instahyre", "Instahyre", 1,
        "Integrated via their public JSON job-search API (jobagent/sources/instahyre.py).",
        search="https://www.instahyre.com/search-jobs/?q={kw}"),
    ExternalSource(
        "unstop", "Unstop (campus drives)", 1,
        "robots.txt explicitly allows /api/public/* but no such public endpoint is published; "
        "the jobs page loads data through an authenticated/obfuscated API. Only category "
        "landing pages are public.",
        kind="campus",
        search="https://unstop.com/competitions?utm_source=JobApply",
        fresher_search="https://unstop.com/job-opportunities"),
    ExternalSource(
        "superset", "Superset (campus placement)", 1,
        "A campus placement platform: drives are visible only to students logged in through "
        "their college. No public listings; robots.txt allows everything but there is "
        "nothing public to read.",
        kind="campus",
        search="https://joinsuperset.com/"),
    # ---- Priority 2 (secondary) ----
    ExternalSource(
        "timesjobs", "TimesJobs", 2,
        "Site unreachable from this environment (TLS failure/blocked); known heavy "
        "anti-bot protection; no public API.",
        search="https://www.timesjobs.com/candidate/job-search-result.html?txtKeywords={kw}"
               "&cboPresFuncArea=0&postWeekDaysValue=1"),
    ExternalSource(
        "shine", "Shine.com", 2,
        "robots.txt disallows /api/* and /jobs/jobs/; search pages are bot-protected.",
        search="https://www.shine.com/job-search/{kw}-jobs"),
    ExternalSource(
        "wellfound", "Wellfound (AngelList Talent)", 2,
        "No public API; job details sit behind login and their ToS prohibits scraping.",
        search="https://wellfound.com/role/r/{kw}"),
    ExternalSource(
        "apna", "Apna", 2,
        "Mobile-app-first platform; no public web jobs API (404 on API paths).",
        search="https://apna.co/jobs"),
    ExternalSource(
        "workindia", "WorkIndia", 2,
        "Site returns HTTP 403 (bot protection); app-first distribution.",
        search="https://www.workindia.in/"),
    ExternalSource(
        "firstnaukri", "FirstNaukri", 2,
        "Site times out / blocks this environment; no public API.",
        search="https://www.firstnaukri.com/fresher-jobs/{kw}"),
    ExternalSource(
        "iimjobs", "iimjobs", 2,
        "Same platform as Hirist: SPA returning 403 to datacenter browsers; no public API.",
        search="https://www.iimjobs.com/jobs?keywords={kw}"),
]


def links_for(keywords: list[str] | None = None) -> list[dict]:
    """Dashboard payload: one entry per source with a ready-to-open search URL."""
    kw = (keywords or ["software engineer fresher"])[0]
    out = []
    for s in SOURCES:
        out.append({
            "key": s.key, "name": s.name, "priority": s.priority, "reason": s.reason,
            "kind": s.kind,
            "search_url": (s.search or "").format(kw=_q(kw)) or None,
            "fresher_url": (s.fresher_search or "").format(kw=_q(kw)) or None,
        })
    return out
