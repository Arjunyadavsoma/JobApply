"""Phase 17.4 — Job verification engine.

Ten checks per job, run with plain HTTP (no browser needed):
  1. HTTP availability          6. company-domain verification
  2. redirect tracking          7. suspicious-domain detection
  3. application-page plaus.    8. salary consistency
  4. expiry detection           9. freshness scoring
  5. duplicate detection       10. source reliability scoring

States: verified_open | probably_open | needs_manual | expired | suspicious | unavailable.
An HTTP 200 alone NEVER yields verified_open — the page body must look like a real
job posting / application page (content check) for that.

Quality score (0-100) is independent of the candidate match score:
  availability 20 + page plausibility 20 + freshness 20 + domain trust 20
  + salary sanity 10 + source reliability 10.
"""
from __future__ import annotations

import ipaddress
import json
import re
import socket
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from jobagent import db
from jobagent.sources.common import log

KNOWN_ATS_DOMAINS = (
    "greenhouse.io", "grnh.se", "lever.co", "ashbyhq.com", "workable.com",
    "myworkdayjobs.com", "wd1.myworkdayjobs.com", "wd3.myworkdayjobs.com", "workday.com",
    "smartrecruiters.com", "icims.com", "taleo.net", "darwinbox.com", "zoho.com",
    "freshteam.com", "successfactors.com", "oraclecloud.com", "eightfold.ai",
    "phenompeople.com", "phenom.com", "mynexthire.com", "amazon.jobs", "instahyre.com",
    "naukri.com", "linkedin.com", "remotive.com", "arbeitnow.com", "himalayas.app",
    "jobicy.com", "unstop.com", "wellfound.com", "cutshort.io", "foundit.in",
    "freshersworld.com", "timesjobs.com", "shine.com", "internshala.com",
)
FREE_MAIL_DOMAINS = ("gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "rediffmail.com",
                     "protonmail.com", "mail.ru", "yandex.com", "gmx.com", "zoho.com")
SHORTENERS = ("bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly", "is.gd", "buff.ly", "cutt.ly",
              "rebrand.ly", "shorturl.at", "tiny.cc")
EXPIRY_MARKERS = (
    "no longer accepting", "position has been filled", "job has expired", "posting closed",
    "no longer available", "not accepting applications", "requisition is closed",
    "this job is closed", "expired", "position filled", "job closed", "already applied",
)
STRONG_EXPIRY = ("no longer accepting", "position has been filled", "job has expired",
                 "not accepting applications", "requisition is closed", "this job is closed",
                 "posting closed", "no longer available")
OPEN_MARKERS = (
    "apply now", "apply for this job", "submit application", "apply for this role",
    "submit your application", "easy apply", "apply today", "submit resume", "apply now,",
    "apply to this job", "start your application",
)
LOOKALIKE_TYPOS = {
    "micros0ft": "microsoft", "g00gle": "google", "amazor": "amazon", "amaz0n": "amazon",
    "microsofl": "microsoft", "googie": "google", "linkegin": "linkedin",
    "lnkedln": "linkedin", "tcs-careers": "tcs", "wipr0": "wipro",
}


@dataclass
class VerifyResult:
    state: str = "needs_manual"            # one of VERIFICATION_STATES
    note: str = ""
    method: str = "http"
    final_url: str | None = None
    redirect_chain: list[str] = field(default_factory=list)
    company_domain: str | None = None
    http_status: int | None = None
    quality_score: int = 0
    components: dict = field(default_factory=dict)
    fraud_flags: list[str] = field(default_factory=list)


def registrable_domain(url: str) -> str:
    """Return the last two labels of the hostname (crisp but adequate for trust checks)."""
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return ""
    if not host:
        return ""
    parts = host.lower().split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "org", "net", "gov", "in", "ac"):
        return ".".join(parts[-3:])          # e.g. foo.co.in
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _is_private_host(url: str) -> bool:
    """SSRF guard: never fetch localhost / private / link-local addresses."""
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return True
    if host in ("localhost", "0.0.0.0", "::1"):
        return True
    try:
        addr = ipaddress.ip_address(host)
        return addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved
    except ValueError:
        pass
    try:
        for fam, _, _, _, sockaddr in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(sockaddr[0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return True
    except (socket.gaierror, OSError):
        return True
    return False


def _domain_trust(url: str, company: str) -> tuple[int, list[str]]:
    """0-20. Known ATS / company career domain = 20; unknown third party = 10;
    free-mail / shortener / raw-IP / lookalike = 0 with flags."""
    flags: list[str] = []
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return 0, ["unparsable apply URL"]
    if not host:
        return 0, ["no hostname in apply URL"]
    dom = registrable_domain(url)

    if re.fullmatch(r"[\d.]+", host):
        return 0, [f"apply URL is a raw IP address ({host})"]
    if any(host == s or host.endswith("." + s) for s in SHORTENERS):
        return 0, [f"apply URL uses a link shortener ({dom})"]
    if dom in FREE_MAIL_DOMAINS:
        return 0, [f"apply link points at a free mail provider ({dom})"]
    if host.startswith("xn--") or "xn--" in host:
        return 0, ["apply URL uses punycode (possible lookalike domain)"]
    squashed = re.sub(r"[^a-z0-9]", "", host)
    for typo, real in LOOKALIKE_TYPOS.items():
        if typo in squashed and real in (company or "").lower():
            return 0, [f"apply domain looks like a typo-squat of {real}"]

    if any(dom == a or dom.endswith("." + a) for a in KNOWN_ATS_DOMAINS):
        return 20, []
    # Company-name heuristic: does the domain relate to the company?
    comp = re.sub(r"[^a-z0-9]", "", (company or "").lower())
    if comp and len(comp) > 3 and comp in dom.replace(".", ""):
        return 20, []
    return 10, []          # unknown third party — not automatically bad, just unproven


def _freshness_score(posted_at: str | None, discovered_at: str) -> int:
    """0-20. Newer is better; undated jobs get a middle score, stale ones low."""
    for raw in (posted_at, discovered_at):
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        days = (datetime.now(timezone.utc) - dt).days
        if days <= 3:
            return 20
        if days <= 7:
            return 17
        if days <= 14:
            return 13
        if days <= 30:
            return 8
        if days <= 60:
            return 4
        return 1
    return 10           # no date known


def _salary_sanity(row) -> tuple[int, str]:
    """0-10. min<=max, plausible fresher-market ranges, no absurd values."""
    smin, smax = row["salary_min"], row["salary_max"]
    text = (row["salary_text"] or "").lower()
    if smin is None and smax is None and not text:
        return 6, "no salary advertised (neutral)"
    if smin is not None and smax is not None and smin > smax:
        return 0, "salary min > max"
    for v in (smin, smax):
        if v is not None and (v < 0.5 or v > 300):        # 0.5 LPA .. 300 LPA
            return 1, f"suspicious salary value ({v} LPA)"
    if smin is not None and smin > 60 and (row["fresher_flag"] or row["experience_min"] == 0):
        return 4, "very high salary claimed for a fresher role"
    return 10, "salary consistent"


def _source_reliability(source: str) -> int:
    """0-10 from data/source_stats.json (recent success rate), floor 5 for never-run."""
    from jobagent.sources import source_health
    stats = source_health().get(source)
    if not stats:
        return 5
    days = stats.get("days", {})
    if not days:
        return 5
    runs = sum(d["ok"] + d["error"] for d in days.values())
    if runs == 0:
        return 5
    ok_ratio = sum(d["ok"] for d in days.values()) / runs
    return max(2, round(ok_ratio * 10))


def _content_check(html: str) -> tuple[str, int]:
    """('open'|'expired'|'unknown', page-plausibility 0-20).
    A bare 200 with no recognisable content is NOT enough for verified_open.
    Strong expiry markers beat generic 'apply now' links (those appear in nav bars too)."""
    text = re.sub(r"<[^>]+>", " ", html or "").lower()
    if not text.strip():
        return "unknown", 0
    hits_expired = sum(2 if m in STRONG_EXPIRY else 1 for m in EXPIRY_MARKERS if m in text)
    hits_open = sum(1 for m in OPEN_MARKERS if m in text)
    # A real posting/application page mentions applying or the job structure.
    plausible = min(20, 6 * hits_open + (6 if re.search(r"\b(job|position|role|opening|requisition)\b", text) else 0))
    if hits_expired >= 2 and hits_expired >= hits_open:
        return "expired", min(12, plausible)
    if hits_open >= 1 and hits_expired < 2:
        return "open", max(14, plausible)
    return "unknown", min(plausible, 6)


def verify_job_row(row, http: httpx.Client | None = None) -> VerifyResult:
    """Run the full check battery on one jobs-table row. Never raises for network errors."""
    res = VerifyResult()
    url = row["apply_url"] or row["url"]
    res.company_domain = registrable_domain(url)

    # --- static checks first (no network) ---
    trust, trust_flags = _domain_trust(url, row["company"] or "")
    res.fraud_flags += trust_flags
    salary_pts, salary_note = _salary_sanity(row)
    fresh_pts = _freshness_score(row["posted_at"], row["discovered_at"])
    rel_pts = _source_reliability(row["source"])
    res.components = {"domain_trust": trust, "freshness": fresh_pts,
                      "salary_sanity": salary_pts, "source_reliability": rel_pts}

    # deadline already in the past -> expired without fetching
    if row["deadline"]:
        try:
            dl = datetime.fromisoformat(str(row["deadline"]))
            if dl.tzinfo is None:
                dl = dl.replace(tzinfo=timezone.utc)
            if dl < datetime.now(timezone.utc):
                res.state, res.note = "expired", "deadline date is in the past"
                res.method = "deadline"
                res.quality_score = trust + 0 + 0 + salary_pts + rel_pts
                return res
        except ValueError:
            pass

    if _is_private_host(url):
        res.state, res.note = "suspicious", "apply URL points at a private/local address"
        res.fraud_flags.append("private or unparsable host in apply URL")
        return res

    # --- HTTP checks (SSRF-guarded: every redirect hop is re-validated) ---
    avail_pts, page_pts, content_state = 0, 0, "unknown"
    own_client = http is None
    client = http or httpx.Client(follow_redirects=True, timeout=20,
                                  headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) JobApply-Verify/1.0"})
    try:
        try:
            from jobagent.security import UrlValidationError, safe_request
            resp = safe_request(url, client=client, headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) JobApply-Verify/1.0"})
            res.http_status = resp.status_code
            res.final_url = str(resp.url)
            res.redirect_chain = [str(h.url) for h in resp.history] + [str(resp.url)]
        except UrlValidationError as e:
            # Redirect landed on a blocked target — treat as suspicious.
            res.state, res.note = "suspicious", f"blocked redirect target: {e.reason}"
            res.fraud_flags.append("redirect to private/local address")
            return res
        except httpx.HTTPError as e:
            res.note = f"HTTP error: {type(e).__name__}"
        if res.http_status is not None:
            if res.http_status in (404, 410):
                avail_pts, content_state = 0, "expired"
                res.note = f"HTTP {res.http_status} (gone)"
            elif 400 <= res.http_status < 500:
                avail_pts, res.note = 0, f"HTTP {res.http_status} (blocked or moved)"
                content_state = "blocked"
            elif res.http_status >= 500:
                avail_pts, res.note = 6, f"HTTP {res.http_status} (server error)"
            else:
                avail_pts = 20
                content_state, page_pts = _content_check(getattr(resp, "text", ""))
                if not res.note:
                    res.note = f"HTTP {res.http_status}, content: {content_state}"
        # redirect red flag: landed somewhere unrelated and off-domain
        if res.final_url and len(res.redirect_chain) > 2:
            dest = registrable_domain(res.final_url)
            if dest and dest != res.company_domain and dest not in KNOWN_ATS_DOMAINS \
                    and trust >= 20:
                trust = 10
                res.note += f"; redirected to unrelated domain ({dest})"
    finally:
        if own_client:
            client.close()

    res.components.update({"availability": avail_pts, "page_plausibility": page_pts})
    res.quality_score = min(100, avail_pts + page_pts + fresh_pts + trust + salary_pts + rel_pts)

    if res.fraud_flags and trust == 0:
        res.state = "suspicious"
    elif content_state == "expired":
        res.state = "expired"
    elif res.http_status is None:
        res.state, res.note = "unavailable", res.note or "could not reach the URL"
    elif avail_pts == 20 and content_state == "open" and page_pts >= 14 and trust >= 10:
        res.state = "verified_open"
    elif avail_pts == 20 and content_state in ("open", "unknown") and trust >= 10:
        res.state = "probably_open"
    else:
        res.state = "needs_manual"
    res.components["salary_note"] = salary_note
    return res


def persist(job_id: str, res: VerifyResult) -> None:
    """Store the verification result + keep legacy `verified` in sync."""
    first = db.get(job_id)
    ts = db.now()
    db.update(
        job_id,
        verification_state=res.state,
        verification_note=res.note[:400],
        verify_method=res.method,
        final_url=res.final_url,
        redirect_chain=res.redirect_chain[:8],
        company_domain=res.company_domain,
        quality_score=res.quality_score,
        verified=1 if res.state in ("verified_open", "probably_open") else 0,
        last_verified_at=ts,
        first_verified_at=(first["first_verified_at"] if first and first["first_verified_at"] else ts),
    )
    db.record_event(job_id, "verify",
                    f"{res.state}: {res.note[:200]} (quality {res.quality_score}/100)")
    if res.fraud_flags:
        try:
            existing = json.loads(first["fraud_flags"] or "[]") if first else []
        except (ValueError, TypeError):
            existing = []
        merged = list(dict.fromkeys(existing + res.fraud_flags))
        db.update(job_id, fraud_flags=merged)


def verify_job(job_id: str) -> VerifyResult:
    row = db.get(job_id)
    if not row:
        raise KeyError(job_id)
    res = verify_job_row(row)
    persist(job_id, res)
    return res


def verify_batch(limit: int = 20, states: tuple[str, ...] | None = None,
                 stale_hours: int = 72) -> tuple[int, dict[str, int]]:
    """Re-verify jobs whose verification is missing or stale. Returns (checked, state counts).

    Priority: unverified first, then oldest last_verified_at. Suspicious and expired
    jobs are NOT re-fetched repeatedly (they only get one re-check after 7 days).
    """
    states = states or ("unverified", "needs_manual", "probably_open", "verified_open")
    q = ("SELECT * FROM jobs WHERE verification_state IS NULL OR verification_state = '' "
         f"OR verification_state IN ({','.join('?' * len(states))}) "
         "ORDER BY CASE WHEN last_verified_at IS NULL THEN 0 ELSE 1 END, last_verified_at ASC "
         "LIMIT ?")
    counts: dict[str, int] = {}
    checked = 0
    with db.connect() as conn:
        rows = conn.execute(q, (*states, limit * 3)).fetchall()
    with httpx.Client(follow_redirects=True, timeout=20,
                      headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) JobApply-Verify/1.0"}) as http:
        for row in rows:
            if checked >= limit:
                break
            # skip fresh verifications unless forced re-check of softer states
            if row["last_verified_at"]:
                try:
                    lv = datetime.fromisoformat(row["last_verified_at"])
                    age_h = (datetime.now(timezone.utc) - lv).total_seconds() / 3600
                except ValueError:
                    age_h = 1e9
                if age_h < stale_hours:
                    continue
            if row["status"] in ("applied", "assessment", "interview", "offer", "rejected",
                                 "withdrawn", "dismissed"):
                continue
            try:
                res = verify_job_row(row, http=http)
                persist(row["id"], res)
                counts[res.state] = counts.get(res.state, 0) + 1
                checked += 1
            except Exception as e:                       # noqa: BLE001 - one job must not kill the batch
                log.warning(f"verify: {row['id']} failed: {e}")
    return checked, counts
