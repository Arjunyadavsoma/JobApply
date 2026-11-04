from __future__ import annotations

import shutil
from collections import Counter
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from jobagent.config import CONFIG_PATH, ROOT

app = typer.Typer(help="AI job application agent: discover -> score -> tailor -> apply (you approve).",
                  no_args_is_help=True, add_completion=False)
console = Console()


@app.command()
def init() -> None:
    """Create config.yaml and .env from the examples."""
    for src, dst in [(ROOT / "config.example.yaml", CONFIG_PATH), (ROOT / ".env.example", ROOT / ".env")]:
        if dst.exists():
            console.print(f"{dst.name} already exists - leaving it alone")
        else:
            shutil.copy(src, dst)
            console.print(f"Created {dst.name} - edit it now")


@app.command("parse-resume")
def parse_resume(pdf: Path = typer.Argument(..., exists=True, dir_okay=False, help="Your current resume PDF")) -> None:
    """Turn your resume PDF into profile/master_resume.json (review and correct it afterwards!)."""
    from jobagent.config import MASTER_RESUME_JSON
    from jobagent.resume import parse_pdf, render_pdf

    from jobagent.config import BASE_RESUME_PDF

    r = parse_pdf(pdf)
    if pdf.resolve() != BASE_RESUME_PDF.resolve():  # keep a copy: it's uploaded when a job isn't tailored
        BASE_RESUME_PDF.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(pdf, BASE_RESUME_PDF)
    preview = render_pdf(r, MASTER_RESUME_JSON.with_name("master_preview.pdf"))
    from jobagent import prefit
    prefit.update(all_jobs=True)  # your skills changed: refresh the quick fit estimates
    console.print(f"[green]Parsed {r.name}: {len(r.experience)} roles, {len(r.projects)} projects.[/green]")
    console.print(f"Check/edit {MASTER_RESUME_JSON} - it's the single source of truth for every tailored resume.")
    console.print(f"Template preview: {preview}")


@app.command()
def discover(source: str = typer.Option(None, help="Comma-separated subset: companies,linkedin,naukri")) -> None:
    """Find new jobs on career portals, LinkedIn and Naukri."""
    from jobagent.sources import discover as run

    run(source)


@app.command()
def score(limit: int = typer.Option(None, help="Max jobs to score this run"),
          job_id: str = typer.Option(None, "--job", help="Score (or re-score) one specific job id")) -> None:
    """Rate new jobs against your resume; low scores are marked skipped."""
    from jobagent.matcher import score_new
    from jobagent.resume import load_master

    score_new(load_master(), limit, job_id)


def tailor_job(job_id: str) -> bool:
    """Tailor one job's resume + cover letter. Returns True on success."""
    from jobagent import db
    from jobagent.llm import LLMError
    from jobagent.resume import load_master, tailor as do_tailor

    job = db.get(job_id)
    if not job:
        console.print("Job not found.")
        return False
    console.print(f"Tailoring: {job['company']} - {job['title']} (match {job['score'] or 'n/a'})")
    try:
        result, pdf, cover = do_tailor(job, load_master())
    except LLMError as e:
        console.print(f"  [red]{e}[/red]")
        return False
    status = job["status"] if job["status"] == "applied" else "tailored"
    db.update(job_id, status=status, resume_path=str(pdf), cover_path=str(cover), changes_json=result.changes,
              error=None)
    console.print(f"  [green]{pdf}[/green]")
    for c in result.changes:
        console.print(f"   - {c}")
    return True


@app.command()
def tailor(limit: int = typer.Option(10, help="Max good matches to tailor this run"),
           job_id: str = typer.Option(None, "--job", help="Tailor one specific job id")) -> None:
    """Write a tailored resume PDF + cover letter (one job, or the top good matches)."""
    from jobagent import db

    ids = [job_id] if job_id else [j["id"] for j in db.by_status("scored")[:limit]]
    for i in ids:
        tailor_job(i)


@app.command()
def apply(limit: int = typer.Option(None, help="Max jobs this session")) -> None:
    """Open each tailored job, auto-fill the form, and let you review + submit."""
    from jobagent.apply import run

    run(limit)


@app.command("apply-one", hidden=True)
def apply_one(job_id: str, tailor_first: bool = typer.Option(False, "--tailor")) -> None:
    """Dashboard-driven apply session for one job (optionally tailoring the resume first)."""
    from jobagent.apply import run_one

    run_one(job_id, tailor_first)


@app.command()
def run() -> None:
    """discover + score in one go. Tailor / apply per job from the dashboard."""
    discover(None)
    score(None, None)
    status()


@app.command(hidden=True)
def search(sources: str = typer.Option(None, help="Comma-separated: companies,linkedin,naukri,remotive,arbeitnow"),
           score_limit: int = typer.Option(0, help="Then score up to N new jobs (0 = don't)"),
           tailor_limit: int = typer.Option(0, help="Then tailor up to N good matches (0 = don't)")) -> None:
    """Dashboard 'Search' button: find jobs, optionally score and tailor them."""
    console.rule("Finding jobs")
    discover(sources)
    if score_limit:
        console.rule("Scoring new jobs")
        score(score_limit, None)
    if tailor_limit:
        console.rule("Tailoring resumes for good matches")
        tailor(tailor_limit, None)
    console.rule("Done")


@app.command()
def enrich(all_jobs: bool = typer.Option(False, "--all", help="Re-enrich every job, not just new ones")) -> None:
    """Extract salary / batch / experience / deadline / fraud flags from stored job text."""
    from jobagent.enrich import enrich_pending

    n = enrich_pending(only_missing=not all_jobs)
    console.print(f"[green]Enriched {n} job(s).[/green]")


@app.command()
def verify(limit: int = typer.Option(20, help="Max stale jobs to re-check")) -> None:
    """Re-check apply URLs of scored/tailored jobs and mark broken ones."""
    import httpx

    from jobagent import db
    rows = [r for r in db.by_status("scored", "tailored") if not r["verified"]][:limit]
    if not rows:
        console.print("Nothing to verify.")
        return
    ok = 0
    with httpx.Client(follow_redirects=True, timeout=20) as http:
        for r in rows:
            try:
                resp = http.head(r["apply_url"] or r["url"])
                if resp.status_code >= 400:
                    resp = http.get(r["apply_url"] or r["url"])
                good = resp.status_code < 400
            except httpx.HTTPError as e:
                good, resp = False, None
            if good:
                ok += 1
                db.update(r["id"], verified=1, last_verified_at=db.now())
            else:
                db.record_event(r["id"], "verify", "Apply URL check failed")
            console.print(f"  {'OK ' if good else 'BAD'} {r['company']} - {r['title']}")
    console.print(f"[green]{ok}/{len(rows)} apply links still work.[/green]")


@app.command()
def analytics() -> None:
    """Show job-search analytics computed from your own data."""
    from jobagent import analytics as A

    d = A.dashboard()
    m = d["metrics"]
    console.rule("Pipeline")
    for k, v in m.items():
        console.print(f"  {k:<20} [b]{v}[/b]")
    console.rule("Applications per week")
    for w in d["applications_over_time"][-8:]:
        bar = "█" * w["applications"]
        console.print(f"  {w['week']}  {bar} {w['applications']}")
    console.rule("Source quality")
    t = Table("source", "jobs", "avg score", "applied", "in progress")
    for s in d["source_quality"]:
        t.add_row(s["source"], str(s["jobs"]), str(s["avg_score"] or "-"), str(s["applied"]),
                  str(s["in_progress"]))
    console.print(t)
    if d["upcoming_deadlines"]:
        console.rule("Upcoming deadlines")
        for dl in d["upcoming_deadlines"][:10]:
            console.print(f"  {dl['deadline']}  {dl['company']} - {dl['title']}")


@app.command()
def status() -> None:
    """Show pipeline counts and the top jobs."""
    from jobagent import db

    rows = db.all_jobs()
    counts = Counter(r["status"] for r in rows)
    console.print("  ".join(f"{k}: [b]{v}[/b]" for k, v in sorted(counts.items())) or "No jobs yet.")
    t = Table("id", "score", "status", "source", "company", "title")
    for r in sorted(rows, key=lambda r: -(r["score"] or 0))[:20]:
        t.add_row(r["id"], str(r["score"] or "-"), r["status"], r["source"], r["company"], r["title"])
    console.print(t)


@app.command()
def web(port: int = typer.Option(8600), open_browser: bool = typer.Option(True)) -> None:
    """Start the dashboard (React app + API) and open it in your browser."""
    import threading
    import webbrowser

    import uvicorn

    if open_browser:
        threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    uvicorn.run("jobagent.server:app", host="127.0.0.1", port=port, log_level="warning")


@app.command()
def dashboard() -> None:
    """Same as `web`."""
    web(8600, True)
