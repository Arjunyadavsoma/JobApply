"""Phase 17.5 — ATS adapter registry for automation.

Platform detection from any job/apply URL + per-platform automation notes
("quirks") that the fill engine and the user both benefit from. The generic
semantic engine in apply.py (labels, aria names, fieldsets, DOM structure -
NOT just CSS selectors) already drives every platform; this registry adds:

  * detect(url) -> platform key + confidence + evidence
  * quirks(platform) -> multi-step hints, submit selectors, known gotchas
  * KNOWN list surfaced to the dashboard / logs

Hard rule preserved everywhere: never bypass CAPTCHA, MFA, login walls or
bot protection. Quirks describe how to be MORE careful, not less.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

# ---------------------------------------------------------------- platforms


@dataclass(frozen=True)
class ATSPlatform:
    key: str
    name: str
    hosts: tuple[str, ...]                     # URL signatures (substring match on host/path)
    multi_step: bool = False                   # forms span several pages/steps
    submit_selectors: tuple[str, ...] = ()
    notes: str = ""
    resume_upload_first: bool = True           # upload resume before other fields (parser prefill)


PLATFORMS: list[ATSPlatform] = [
    ATSPlatform("greenhouse", "Greenhouse",
                ("boards.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io",
                 "grnh.se"),
                multi_step=True,
                submit_selectors=("button[type='submit']", "#submit_app"),
                notes="Multi-page form; 'Next' between steps. EEO questions usually optional. "
                      "Resume upload often pre-fills profile fields."),
    ATSPlatform("lever", "Lever",
                ("jobs.lever.co", "lever.co"),
                submit_selectors=("button[type='submit']",),
                notes="Single page usually; resume upload triggers parsing + autofill."),
    ATSPlatform("ashby", "Ashby",
                ("jobs.ashbyhq.com", "ashbyhq.com"),
                multi_step=True,
                submit_selectors=("button[type='submit']",),
                notes="Multi-step with custom dropdowns; the fill engine's combobox "
                      "handling covers them."),
    ATSPlatform("workday", "Workday",
                ("myworkdayjobs.com", "wd1.myworkdayjobs.com", "wd3.myworkdayjobs.com",
                 "workday.com"),
                multi_step=True,
                submit_selectors=("button[data-automation-id='bottom-navigation-next-button']",
                                  "button[data-automation-id='bottom-navigation-submit-button']"),
                notes="Heavy iframe usage and data-automation-id attributes; steps are "
                      "driven by Next buttons. Slow to load - extra waits are normal."),
    ATSPlatform("smartrecruiters", "SmartRecruiters",
                ("smartrecruiters.com", "careers.smartrecruiters.com"),
                submit_selectors=("button[type='submit']",),
                notes="Usually one page; file upload + a few dropdowns."),
    ATSPlatform("icims", "iCIMS",
                ("icims.com",),
                multi_step=True,
                submit_selectors=("input[type='submit']", "button[type='submit']"),
                notes="Older DOM, real <input type=submit>; profile forms in frames."),
    ATSPlatform("taleo", "Oracle Taleo",
                ("taleo.net",),
                multi_step=True,
                submit_selectors=("input[type='submit']", "#submit"),
                notes="Legacy UI, many required dropdowns, session timeouts are common."),
    ATSPlatform("darwinbox", "Darwinbox",
                ("darwinbox.com", "darwinbox.in"),
                multi_step=True,
                notes="India-native ATS; forms inside authenticated portals mostly - "
                      "apply manually when a login is required."),
    ATSPlatform("zoho_recruit", "Zoho Recruit / Freshteam",
                ("zoho.com/recruit", "zohorecruit.com", "freshteam.com"),
                submit_selectors=("button[type='submit']",),
                notes="Simple forms; India SMBs use these a lot."),
    ATSPlatform("successfactors", "SAP SuccessFactors",
                ("successfactors.com", "careers.sap.com"),
                multi_step=True,
                submit_selectors=("button[type='submit']",),
                notes="Long multi-step; save-and-return supported."),
    ATSPlatform("oracle_recruiting", "Oracle Recruiting Cloud",
                ("oraclecloud.com",),
                multi_step=True,
                notes="Similar flow to Workday; watch for country-specific fields."),
    ATSPlatform("eightfold", "Eightfold AI",
                ("eightfold.ai", "myeightfold"),
                notes="Resume-first flow: upload, review parsed fields, then submit."),
    ATSPlatform("phenom", "Phenom People",
                ("phenompeople.com", "phenom.com"),
                notes="Career-site wrapper; apply usually lands on an underlying ATS "
                      "(SmartRecruiters/Workday) - detection follows the redirect."),
    ATSPlatform("workable", "Workable",
                ("workable.com", "apply.workable.com"),
                submit_selectors=("button[type='submit']",),
                notes="Clean single-page form, semantic labels."),
    ATSPlatform("mynexthire", "MyNextHire",
                ("mynexthire.com",),
                notes="India campus-hiring ATS used by several MNCs."),
    ATSPlatform("linkedin", "LinkedIn Easy Apply",
                ("linkedin.com",),
                multi_step=True,
                notes="Easy Apply walks several steps; the agent fills but never clicks "
                      "Submit (profile-based, applies instantly)."),
    ATSPlatform("naukri", "Naukri.com",
                ("naukri.com",),
                notes="Apply uses your Naukri profile and submits instantly - never "
                      "auto-clicked; log in and review yourself."),
    ATSPlatform("instahyre", "Instahyre",
                ("instahyre.com",),
                notes="One-click apply with your Instahyre profile - never auto-clicked."),
    ATSPlatform("generic", "Generic company portal",
                (),
                notes="No known signature - the semantic field engine handles it from "
                      "labels, aria names and DOM structure."),
]

BY_KEY = {p.key: p for p in PLATFORMS}


def detect(url: str) -> dict:
    """Identify the ATS behind a URL. Falls back to 'generic' (the engine still works)."""
    if not url:
        return {"platform": "generic", "name": "Generic company portal", "confidence": 0.0,
                "evidence": "no URL"}
    try:
        host = (urlparse(url).hostname or "").lower()
        path = (urlparse(url).path or "").lower()
    except ValueError:
        return {"platform": "generic", "name": "Generic company portal", "confidence": 0.0,
                "evidence": "unparsable URL"}
    blob = f"{host}/{path}"
    for p in PLATFORMS:
        for sig in p.hosts:
            if sig in blob:
                return {"platform": p.key, "name": p.name, "confidence": 0.95,
                        "evidence": f"host/path matches {sig}",
                        "multi_step": p.multi_step,
                        "submit_selectors": list(p.submit_selectors),
                        "notes": p.notes, "resume_upload_first": p.resume_upload_first}
    return {"platform": "generic", "name": BY_KEY["generic"].name, "confidence": 0.3,
            "evidence": "no known signature; semantic engine will drive",
            "multi_step": False, "submit_selectors": [],
            "notes": BY_KEY["generic"].notes, "resume_upload_first": True}


def quirks(platform_key: str) -> dict:
    p = BY_KEY.get(platform_key)
    if not p:
        return {"platform": "generic", **detect("")}
    return {"platform": p.key, "name": p.name, "multi_step": p.multi_step,
            "submit_selectors": list(p.submit_selectors), "notes": p.notes,
            "resume_upload_first": p.resume_upload_first}


def known_platforms() -> list[dict]:
    return [{"key": p.key, "name": p.name, "multi_step": p.multi_step, "hosts": list(p.hosts)}
            for p in PLATFORMS if p.key != "generic"]
