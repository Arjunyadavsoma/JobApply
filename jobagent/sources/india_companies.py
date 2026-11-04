"""Curated India-first company career registry.

Maps major Indian and global employers hiring in India to the portal adapters the agent already
supports (workday / smartrecruiters / workable / eightfold / oracle / mynexthire / greenhouse /
lever / ashby / successfactors / generic). This is DATA, not scraping: each entry is the
company's own public careers URL, exactly what a person would open in a browser.

Used to seed `sources.companies` when the user hasn't configured their own list, so an
India-first fresher gets MNC career pages + Indian product companies out of the box.
"""
from __future__ import annotations

# Entries: (company, careers URL, kind) where kind matches an existing adapter.
#   workday / smartrecruiters / workable / portal (generic career-site search) / eightfold / oracle
REGISTRY: list[tuple[str, str, str]] = [
    # ---- global MNCs with large India engineering offices ----
    ("Microsoft", "https://careers.microsoft.com/", "eightfold"),
    ("Amazon", "https://www.amazon.jobs/en/", "portal"),
    ("Google", "https://www.google.com/about/careers/applications/jobs/results/", "portal"),
    ("Oracle", "https://careers.oracle.com/jobs/", "oracle"),
    ("NVIDIA", "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite", "workday"),
    ("Adobe", "https://adobe.wd5.myworkdayjobs.com/external_experienced", "workday"),
    ("Salesforce", "https://careers.salesforce.com/en/jobs/", "portal"),
    ("ServiceNow", "https://jobs.smartrecruiters.com/ServiceNow", "smartrecruiters"),
    ("Qualcomm", "https://careers.qualcomm.com/careers", "eightfold"),
    ("SAP", "https://jobs.sap.com/", "portal"),
    ("IBM", "https://www.ibm.com/careers/search", "portal"),
    ("EA", "https://ea.gr8people.com/jobs", "portal"),
    # ---- India IT services + consulting (big fresher recruiters) ----
    ("Accenture", "https://accenture.jobs/", "portal"),
    ("Deloitte India", "https://www2.deloitte.com/in/en/careers.html", "portal"),
    ("PwC India", "https://www.pwc.in/careers.html", "portal"),
    ("EY India", "https://www.ey.com/en_in/careers", "portal"),
    ("KPMG India", "https://kpmg.com/in/en/careers.html", "portal"),
    # ---- India product companies / startups that hire freshers + interns ----
    ("Freshworks", "https://www.freshworks.com/company/careers/", "portal"),
    ("Zoho", "https://www.zoho.com/careers/", "portal"),
    ("Razorpay", "https://razorpay.com/jobs/", "portal"),
    ("PhonePe", "https://www.phonepe.com/careers/", "portal"),
    ("Swiggy", "https://www.swiggy.com/careers", "portal"),
    ("Flipkart", "https://www.flipkartcareers.com/", "portal"),
    ("CRED", "https://careers.cred.club/", "portal"),
]

# Greenhouse/Lever/Ashby boards of India-relevant tech companies (public board slugs).
GREENHOUSE_BOARDS = ["razorpay", "databricks", "postman", "hasura"]
LEVER_BOARDS = ["plivo", "exotel"]
ASHBY_BOARDS = ["ramp"]


def seed_config(companies_cfg: dict) -> dict:
    """Merge the registry into the user's sources.companies config (user entries win)."""
    cfg = dict(companies_cfg or {})
    portals = [p for p in (cfg.get("portals") or [])]
    have = {str(p).split("|")[0].strip().lower() for p in portals}
    for company, url, _kind in REGISTRY:
        if company.lower() not in have:
            portals.append(f"{company} | {url}")
    cfg["portals"] = portals
    for board, key in ((GREENHOUSE_BOARDS, "greenhouse"), (LEVER_BOARDS, "lever"), (ASHBY_BOARDS, "ashby")):
        existing = set(cfg.get(key) or [])
        cfg[key] = sorted(existing | set(board))
    return cfg


def fresher_sources_ready() -> bool:
    """True when the config already has portals/companies configured by the user."""
    from jobagent.config import cfg
    return bool(cfg("sources.companies.portals", []) or cfg("sources.companies.workday", []))
