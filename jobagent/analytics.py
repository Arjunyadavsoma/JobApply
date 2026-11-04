"""Analytics over persisted data - every number comes from the jobs/events tables.

No fabricated statistics: when there is not enough data, charts simply show what exists.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from jobagent import db


def _week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def applications_over_time(weeks: int = 12) -> list[dict]:
    """Applications per week (by applied_at), oldest first."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT applied_at FROM jobs WHERE status != 'applied' OR applied_at IS NOT NULL"
        ).fetchall()
    counts: dict[str, int] = {}
    for r in rows:
        ts = r["applied_at"]
        if not ts:
            continue
        try:
            d = datetime.fromisoformat(ts).date()
        except ValueError:
            continue
        counts[_week_start(d).isoformat()] = counts.get(_week_start(d).isoformat(), 0) + 1
    today = date.today()
    out = []
    for i in range(weeks - 1, -1, -1):
        ws = _week_start(today - timedelta(weeks=i)).isoformat()
        out.append({"week": ws, "applications": counts.get(ws, 0)})
    return out


def status_funnel() -> dict[str, int]:
    s = db.stats()
    return {
        "discovered": s["total"],
        "matched": s["scored"] + s["tailored"] + s["applied"] + s["assessment"] +
                   s["interview"] + s["offer"] + s["rejected"] + s["withdrawn"],
        "ready": s["tailored"],
        "applied": s["applied"] + s["assessment"] + s["interview"] + s["offer"] +
                   s["rejected"] + s["withdrawn"],
        "in_progress": s["assessment"] + s["interview"],
        "offers": s["offer"],
        "outcomes": s["rejected"] + s["withdrawn"] + s["offer"],
    }


def match_distribution() -> list[dict]:
    """Score buckets for scored jobs: 0-49 / 50-69 / 70-84 / 85-100."""
    with db.connect() as conn:
        rows = conn.execute("SELECT score FROM jobs WHERE score IS NOT NULL").fetchall()
    buckets = {"0-49": 0, "50-69": 0, "70-84": 0, "85-100": 0}
    for r in rows:
        s = r["score"]
        k = "0-49" if s < 50 else "50-69" if s < 70 else "70-84" if s < 85 else "85-100"
        buckets[k] += 1
    return [{"bucket": k, "jobs": v} for k, v in buckets.items()]


def source_quality() -> list[dict]:
    """Per source: jobs found, avg AI score, applied count, in-progress count."""
    with db.connect() as conn:
        rows = conn.execute("""
            SELECT source,
                   COUNT(*) jobs,
                   AVG(score) avg_score,
                   SUM(CASE WHEN status IN ('applied','assessment','interview','offer',
                                            'rejected','withdrawn') THEN 1 ELSE 0 END) applied,
                   SUM(CASE WHEN status IN ('assessment','interview','offer') THEN 1 ELSE 0 END) in_progress
            FROM jobs GROUP BY source ORDER BY jobs DESC
        """).fetchall()
    out = []
    for r in rows:
        out.append({
            "source": r["source"], "jobs": r["jobs"],
            "avg_score": round(r["avg_score"]) if r["avg_score"] is not None else None,
            "applied": r["applied"] or 0, "in_progress": r["in_progress"] or 0,
            "response_rate": round(100 * (r["in_progress"] or 0) / r["applied"]) if r["applied"] else 0,
        })
    return out


def missing_skills(top: int = 10) -> list[dict]:
    """Most common missing skills across scored jobs (from stored match_json)."""
    with db.connect() as conn:
        rows = conn.execute("SELECT match_json FROM jobs WHERE match_json IS NOT NULL").fetchall()
    counts: dict[str, int] = {}
    for r in rows:
        try:
            m = json.loads(r["match_json"])
        except ValueError:
            continue
        for s in (m.get("missing_skills") or m.get("missing_requirements") or [])[:8]:
            key = str(s).strip()[:60]
            if key:
                counts[key] = counts.get(key, 0) + 1
    return [{"skill": k, "jobs": v} for k, v in sorted(counts.items(), key=lambda x: -x[1])[:top]]


def eligibility_breakdown() -> list[dict]:
    """Eligibility status counts (from stored match_json.eligibility)."""
    with db.connect() as conn:
        rows = conn.execute("SELECT match_json FROM jobs WHERE match_json IS NOT NULL").fetchall()
    counts = {"eligible": 0, "likely_eligible": 0, "needs_verification": 0, "not_eligible": 0}
    for r in rows:
        try:
            m = json.loads(r["match_json"])
            status = (m.get("eligibility") or {}).get("status")
            if status in counts:
                counts[status] += 1
        except ValueError:
            continue
    return [{"status": k, "jobs": v} for k, v in counts.items()]


def upcoming_deadlines(days: int = 14) -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, company, title, deadline, status FROM jobs"
            " WHERE deadline IS NOT NULL AND deadline != ''"
            " AND date(deadline) BETWEEN date('now') AND date('now', ?)"
            " AND status NOT IN ('applied','rejected','withdrawn','expired','dismissed')"
            " ORDER BY deadline", (f"+{days} days",),
        ).fetchall()
    return [dict(r) for r in rows]


def dashboard() -> dict:
    """One call for the dashboard: metrics + charts."""
    s = db.stats()
    active = s["new"] + s["scored"] + s["tailored"]
    return {
        "metrics": {
            "jobs_discovered": s["total"],
            "needs_scoring": s["new"],
            "good_matches": s["scored"],
            "resumes_ready": s["tailored"],
            "applied": s["applied"] + s["assessment"] + s["interview"] + s["offer"] +
                       s["rejected"] + s["withdrawn"],
            "in_progress": s["assessment"] + s["interview"],
            "offers": s["offer"],
            "active_pipeline": active,
            "upcoming_deadlines": len(upcoming_deadlines()),
        },
        "applications_over_time": applications_over_time(),
        "status_funnel": status_funnel(),
        "match_distribution": match_distribution(),
        "source_quality": source_quality(),
        "missing_skills": missing_skills(),
        "eligibility": eligibility_breakdown(),
        "upcoming_deadlines": upcoming_deadlines(),
    }
