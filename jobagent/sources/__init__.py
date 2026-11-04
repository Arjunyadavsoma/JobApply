"""Discovery: pull jobs from every enabled source, filter by title/location, store + enrich.

Sources: company career portals (ATS adapters), LinkedIn guest search, Naukri, Instahyre,
Arbeitnow and Remotive (public remote-job APIs). On first run the India company registry seeds
a default portal list so a fresher gets MNC career pages out of the box.
"""
from __future__ import annotations

import json
import re
import time

import httpx

from jobagent import db
from jobagent.config import DATA_DIR, cfg
from jobagent.db import Job
from jobagent.sources import ats, linkedin
from jobagent.sources.common import USER_AGENT, log
from jobagent.sources.location import keep_location

SOURCE_STATS = DATA_DIR / "source_stats.json"


def _keep(job: Job) -> bool:
    if not keep_location(job.location, job.source, cfg("search.location_mode", "any")):
        return False
    title = job.title.lower()
    if any(x.lower() in title for x in cfg("search.title_exclude", [])):
        return False
    # Keyword searches (LinkedIn/Naukri) are already targeted; career portals list every role.
    if job.source in ats.ALL_ATS or job.source in ("arbeitnow", "remotive"):
        must = [x.lower() for x in cfg("search.title_must_include", [])]
        if must and not any(x in title for x in must):
            return False
        locs = [x.lower() for x in cfg("search.location_include", [])]
        if locs and not any(x in (job.location or "").lower() for x in locs):
            return False
    return bool(job.description and job.description.strip())


def _record_source_health(results: dict[str, tuple[int, str]]) -> None:
    """results: source -> (jobs found, 'ok' | error text). Kept across runs in data/."""
    stats: dict = {}
    try:
        stats = json.loads(SOURCE_STATS.read_text(encoding="utf-8")) if SOURCE_STATS.exists() else {}
    except (ValueError, OSError):
        stats = {}
    day = db.now()[:10]
    for source, (count, status) in results.items():
        s = stats.setdefault(source, {"days": {}})
        day_rec = s["days"].setdefault(day, {"ok": 0, "error": 0, "jobs": 0})
        day_rec["jobs"] += count
        if status == "ok":
            day_rec["ok"] += 1
        else:
            day_rec["error"] += 1
            s["last_error"] = status[:200]
        s["last_run"] = day
        if status == "ok":
            s.pop("last_error", None)
    # keep only the last 14 days per source
    for source, s in stats.items():
        s["days"] = {d: v for d, v in sorted(s["days"].items())[-14:]}
    try:
        SOURCE_STATS.parent.mkdir(parents=True, exist_ok=True)
        SOURCE_STATS.write_text(json.dumps(stats, indent=1), encoding="utf-8")
    except OSError:
        pass


def source_health() -> dict:
    try:
        return json.loads(SOURCE_STATS.read_text(encoding="utf-8")) if SOURCE_STATS.exists() else {}
    except (ValueError, OSError):
        return {}


def discover(only: str | None = None) -> int:
    """`only`: None for every enabled source, or a comma-separated subset of
    companies,linkedin,naukri,instahyre,remotive,arbeitnow,himalayas,jobicy."""
    wanted = set(only.split(",")) if only else {
        "companies", "linkedin", "naukri", "instahyre", "remotive", "arbeitnow",
        "himalayas", "jobicy"}
    keywords = cfg("search.keywords", [])
    locations = cfg("search.locations", [])
    jobs: list[Job] = []
    health: dict[str, tuple[int, str]] = {}

    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True,
                      transport=httpx.HTTPTransport(retries=3)) as http:

        # ---- concurrent simple-API fetchers (each is independent + rate-limited inside) ----
        def _run(name: str, fn) -> None:
            try:
                found = fn()
                jobs.extend(found)
                health[name] = (len(found), "ok")
            except Exception as e:                      # noqa: BLE001 - one bad source must not kill the run
                health[name] = (0, str(e))
                log.warning(f"{name} failed: {e}")

        simple: list[tuple[str, object]] = []

        if "remotive" in wanted and (only or cfg("sources.remotive.enabled", True)):
            from jobagent.sources import remotive
            simple.append(("remotive", lambda: remotive.search(cfg("sources.remotive.pages", 1), http, keywords)))
        if "arbeitnow" in wanted and (only or cfg("sources.arbeitnow.enabled", True)):
            from jobagent.sources import arbeitnow
            simple.append(("arbeitnow", lambda: arbeitnow.search(cfg("sources.arbeitnow.pages", 1), http, keywords)))
        if "himalayas" in wanted and (only or cfg("sources.himalayas.enabled", True)):
            from jobagent.sources import himalayas
            simple.append(("himalayas", lambda: himalayas.search(cfg("sources.himalayas.pages", 1), http, keywords)))
        if "jobicy" in wanted and (only or cfg("sources.jobicy.enabled", True)):
            from jobagent.sources import jobicy
            simple.append(("jobicy", lambda: jobicy.search(cfg("sources.jobicy.pages", 1), http, keywords)))

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=cfg("search.ingest_concurrency", 4),
                                thread_name_prefix="ingest") as pool:
            futures = {pool.submit(_run, name, fn): name for name, fn in simple}
            for fut in futures:
                fut.result()          # _run never raises; join before the browser-dependent part

        if "linkedin" in wanted and (only or cfg("sources.linkedin.enabled", True)):
            try:
                found = linkedin.search(keywords, locations, cfg("sources.linkedin.pages", 1),
                                        cfg("search.max_age_days", 14), http)
                jobs += found
                health["linkedin"] = (len(found), "ok")
            except Exception as e:                      # noqa: BLE001
                health["linkedin"] = (0, str(e))
                log.warning(f"linkedin failed: {e}")
        if "instahyre" in wanted and (only or cfg("sources.instahyre.enabled", True)):
            try:
                from jobagent.sources import instahyre
                years = cfg("sources.instahyre.years")
                if years in (None, ""):
                    m = re.search(r"\d+", str(cfg("applicant.total_experience_years", "")))
                    years = int(m.group()) if m else None
                found = instahyre.search(cfg("sources.instahyre.skills", []) or keywords,
                                         cfg("sources.instahyre.job_functions", []), years,
                                         cfg("sources.instahyre.pages", 3), http)
                jobs += found
                health["instahyre"] = (len(found), "ok")
            except Exception as e:                      # noqa: BLE001
                health["instahyre"] = (0, str(e))
                log.warning(f"instahyre failed: {e}")
        if "companies" in wanted:
            companies_cfg = cfg("sources.companies", {}) or {}
            from jobagent.sources import india_companies
            if not companies_cfg.get("portals") and not companies_cfg.get("workday") \
                    and cfg("search.use_india_registry", True):
                companies_cfg = india_companies.seed_config(companies_cfg)
                log.info(f"india registry: seeded {len(india_companies.REGISTRY)} company career pages")
            try:
                found = ats.fetch_companies(companies_cfg, http, keywords)
                from jobagent.sources import portals
                found += portals.fetch_portals(companies_cfg.get("portals", []), http, keywords)
                jobs += found
                health["career_portals"] = (len(found), "ok")
            except Exception as e:                      # noqa: BLE001 - one bad source must not kill the run
                health["career_portals"] = (0, str(e))
                log.warning(f"career portals failed: {e}")

    if "naukri" in wanted and (only or cfg("sources.naukri.enabled", True)):
        try:
            from jobagent.browser import browser_context
            from jobagent.sources import naukri
            with browser_context() as ctx:
                found = naukri.search(keywords, locations, cfg("sources.naukri.pages", 1), ctx)
            jobs += found
            health["naukri"] = (len(found), "ok")
        except Exception as e:                          # noqa: BLE001
            health["naukri"] = (0, str(e))
            log.warning(f"naukri failed: {e}")

    kept = [j for j in jobs if _keep(j)]
    inserted_ids: list[str] = []
    with db.connect() as conn:
        for j in kept:
            if not conn.execute("SELECT 1 FROM jobs WHERE id = ?", (j.id,)).fetchone() and \
               not conn.execute("SELECT 1 FROM jobs WHERE lower(company)=lower(?) AND lower(title)=lower(?)",
                                (j.company, j.title)).fetchone():
                inserted_ids.append(j.id)
    inserted = db.upsert_jobs(kept)

    # Deterministic enrichment (salary / batch / experience / deadlines / fraud flags)
    from jobagent import enrich as enrichment
    enriched = 0
    for jid in inserted_ids:
        row = db.get(jid)
        if row:
            facts = enrichment.enrich(row["title"], row["description"] or "", row["company"] or "",
                                      row["apply_url"] or "")
            enrichment.apply_to_job(jid, facts)
            enriched += 1
            if facts.fraud_flags:
                db.add_notification("system", f"Review before applying: {row['company']} - {row['title']}",
                                    "; ".join(facts.fraud_flags), jid)

    from jobagent import prefit
    prefit.update()
    _record_source_health(health)

    # Phase 17.4: verify newly discovered jobs (HTTP + content + domain checks)
    verified_states: dict[str, int] = {}
    if inserted_ids:
        from jobagent import verify as verify_engine
        import httpx as _httpx
        with _httpx.Client(follow_redirects=True, timeout=15,
                           headers={"User-Agent": USER_AGENT}) as vhttp:
            for jid in inserted_ids[: cfg("search.verify_new_limit", 25)]:
                row = db.get(jid)
                if not row:
                    continue
                try:
                    res = verify_engine.verify_job_row(row, http=vhttp)
                    verify_engine.persist(jid, res)
                    verified_states[res.state] = verified_states.get(res.state, 0) + 1
                except Exception as e:                    # noqa: BLE001
                    log.debug(f"verify {jid}: {e}")
                import time as _t
                _t.sleep(0.4)                            # be a polite client

    log.info(f"Discovered {len(jobs)} jobs, {len(kept)} passed filters, {inserted} new "
             f"({enriched} enriched, {verified_states or 'no verification'})")
    return inserted
