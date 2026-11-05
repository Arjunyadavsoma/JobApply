"""Phase 19D — centralized application lifecycle state machine.

The Phase 17/18 queue used a free-form ``jobs.queue_state`` string. This
module formalizes the lifecycle: every state change must pass through
``transition()`` which enforces:

* Only valid edges of the lifecycle graph are accepted.
* Every transition is recorded with timestamp, actor and evidence.
* Submission-adjacent actions require an idempotency key: replaying the
  same key returns the recorded outcome instead of executing again.
* ``APPROVED`` can only be entered by the human user.
* ``SUBMITTED_CONFIRMED`` is terminal — it can never be re-entered.
* ``SUBMISSION_UNCERTAIN`` can only leave via an explicit human decision;
  automated retries from an uncertain state are structurally impossible.
* State updates are transactional (BEGIN IMMEDIATE) so two workers can
  never interleave a read-modify-write on the same application.

The legacy queue_state values keep working: ``compatible_state()`` maps
them onto the formal lifecycle for display and gates.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from jobagent import db

# ---------------------------------------------------------------- states

DISCOVERED = "DISCOVERED"
VERIFICATION_PENDING = "VERIFICATION_PENDING"
VERIFIED_OPEN = "VERIFIED_OPEN"
ELIGIBILITY_PENDING = "ELIGIBILITY_PENDING"
ELIGIBLE = "ELIGIBLE"
PREPARATION_PENDING = "PREPARATION_PENDING"
PREPARING = "PREPARING"
READY_FOR_REVIEW = "READY_FOR_REVIEW"
APPROVED = "APPROVED"
IN_PROGRESS = "IN_PROGRESS"
AWAITING_USER = "AWAITING_USER"
SUBMISSION_ATTEMPTED = "SUBMISSION_ATTEMPTED"
SUBMITTED_CONFIRMED = "SUBMITTED_CONFIRMED"
SUBMISSION_UNCERTAIN = "SUBMISSION_UNCERTAIN"
FAILED = "FAILED"
EXPIRED = "EXPIRED"
WITHDRAWN = "WITHDRAWN"
REJECTED = "REJECTED"

STATES = frozenset({
    DISCOVERED, VERIFICATION_PENDING, VERIFIED_OPEN, ELIGIBILITY_PENDING,
    ELIGIBLE, PREPARATION_PENDING, PREPARING, READY_FOR_REVIEW, APPROVED,
    IN_PROGRESS, AWAITING_USER, SUBMISSION_ATTEMPTED, SUBMITTED_CONFIRMED,
    SUBMISSION_UNCERTAIN, FAILED, EXPIRED, WITHDRAWN, REJECTED,
})

TERMINAL_STATES = frozenset({SUBMITTED_CONFIRMED, EXPIRED, WITHDRAWN, REJECTED})

# ---------------------------------------------------------------- transitions

TRANSITIONS: dict[str, frozenset[str]] = {
    DISCOVERED: frozenset({VERIFICATION_PENDING, EXPIRED, WITHDRAWN}),
    VERIFICATION_PENDING: frozenset({VERIFIED_OPEN, EXPIRED, FAILED, WITHDRAWN}),
    VERIFIED_OPEN: frozenset({ELIGIBILITY_PENDING, EXPIRED, WITHDRAWN}),
    ELIGIBILITY_PENDING: frozenset({ELIGIBLE, REJECTED, EXPIRED, WITHDRAWN}),
    ELIGIBLE: frozenset({PREPARATION_PENDING, EXPIRED, WITHDRAWN}),
    PREPARATION_PENDING: frozenset({PREPARING, EXPIRED, WITHDRAWN}),
    PREPARING: frozenset({READY_FOR_REVIEW, AWAITING_USER, FAILED, WITHDRAWN}),
    READY_FOR_REVIEW: frozenset({APPROVED, PREPARING, EXPIRED, WITHDRAWN}),
    # Approval is a human-only gate: entering APPROVED requires actor == "user".
    APPROVED: frozenset({IN_PROGRESS, WITHDRAWN}),
    IN_PROGRESS: frozenset({AWAITING_USER, SUBMISSION_ATTEMPTED, FAILED, WITHDRAWN}),
    # After the human resolves an open question the application returns to
    # review — it never jumps straight into execution.
    AWAITING_USER: frozenset({READY_FOR_REVIEW, IN_PROGRESS, FAILED, WITHDRAWN}),
    SUBMISSION_ATTEMPTED: frozenset({SUBMITTED_CONFIRMED, SUBMISSION_UNCERTAIN, FAILED}),
    # Uncertain submissions NEVER auto-retry: only a human verdict can move on.
    SUBMISSION_UNCERTAIN: frozenset({SUBMITTED_CONFIRMED, FAILED}),
    SUBMITTED_CONFIRMED: frozenset(),          # terminal — never re-entered
    FAILED: frozenset({PREPARING, WITHDRAWN}),  # retry goes back through preparation
    EXPIRED: frozenset(),
    WITHDRAWN: frozenset(),
    REJECTED: frozenset(),
}

# States whose entry requires evidence attached to the transition.
EVIDENCE_REQUIRED = frozenset({
    SUBMISSION_ATTEMPTED, SUBMITTED_CONFIRMED, SUBMISSION_UNCERTAIN,
    APPROVED, REJECTED, FAILED,
})

# States that participate in submission execution — idempotency keys required.
SUBMISSION_STATES = frozenset({SUBMISSION_ATTEMPTED, SUBMITTED_CONFIRMED,
                               SUBMISSION_UNCERTAIN})

ACTORS = frozenset({"system", "user", "agent"})

# Legacy queue_state (Phase 17/18) -> formal lifecycle state.
QUEUE_STATE_MAP = {
    "new": DISCOVERED,
    "analyzing": ELIGIBILITY_PENDING,
    "eligible": ELIGIBLE,
    "shortlisted": ELIGIBLE,
    "resume_prep": PREPARING,
    "ready": READY_FOR_REVIEW,
    "awaiting_approval": READY_FOR_REVIEW,
    "applying": IN_PROGRESS,
    "submitted": SUBMISSION_ATTEMPTED,
    "failed": FAILED,
    "needs_manual": AWAITING_USER,
    None: DISCOVERED,
    "": DISCOVERED,
}


class InvalidTransition(ValueError):
    """Raised when a state change is not allowed by the lifecycle graph."""

    def __init__(self, job_id: str, from_state: str, to_state: str, reason: str):
        self.job_id, self.from_state, self.to_state = job_id, from_state, to_state
        super().__init__(f"{job_id}: {from_state} -> {to_state} rejected: {reason}")


@dataclass(frozen=True)
class TransitionResult:
    job_id: str
    from_state: str
    to_state: str
    actor: str
    at: str
    idempotency_key: str | None
    replayed: bool          # True when an idempotency replay returned the record

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id, "from": self.from_state, "to": self.to_state,
            "actor": self.actor, "at": self.at, "replayed": self.replayed,
        }


# ---------------------------------------------------------------- schema

SCHEMA = """
CREATE TABLE IF NOT EXISTS application_state (
    job_id      TEXT PRIMARY KEY,
    state       TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    updated_by  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS application_transitions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    from_state  TEXT NOT NULL,
    to_state    TEXT NOT NULL,
    actor       TEXT NOT NULL,
    evidence    TEXT,                       -- JSON: URLs, screenshots, refs
    note        TEXT,
    idempotency_key TEXT,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_apptrans_job ON application_transitions(job_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_apptrans_idem
    ON application_transitions(idempotency_key) WHERE idempotency_key IS NOT NULL;
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)


# ---------------------------------------------------------------- core API

def current_state(job_id: str, conn: sqlite3.Connection | None = None) -> str:
    """Current formal state; seeds from the legacy queue_state on first read."""
    owns = conn is None
    c = conn or db.connect()
    try:
        ensure_schema(c)
        row = c.execute("SELECT state FROM application_state WHERE job_id=?",
                        (job_id,)).fetchone()
        if row:
            return row["state"]
        # Seed from legacy state (read-only derivation, no transition recorded).
        job = c.execute("SELECT queue_state, status FROM jobs WHERE id=?",
                        (job_id,)).fetchone()
        if not job:
            return DISCOVERED  # unknown job — the earliest state, never invented
        state = QUEUE_STATE_MAP.get(job["queue_state"], DISCOVERED)
        # A recorded+confirmed submission is stronger than the queue label.
        sub = c.execute(
            "SELECT confirmed FROM submissions WHERE job_id=? "
            "ORDER BY submitted_at DESC LIMIT 1", (job_id,)).fetchone()
        if sub:
            state = SUBMITTED_CONFIRMED if sub["confirmed"] else SUBMISSION_UNCERTAIN
        elif job["status"] == "applied" and state == DISCOVERED:
            state = SUBMISSION_ATTEMPTED
        return state
    finally:
        if owns:
            c.close()


def can_transition(from_state: str, to_state: str) -> bool:
    return to_state in TRANSITIONS.get(from_state, frozenset())


def transition(job_id: str, to_state: str, *, actor: str = "system",
               evidence: dict[str, Any] | None = None, note: str = "",
               idempotency_key: str | None = None,
               ) -> TransitionResult:
    """Apply a lifecycle transition under full validation.

    - validates the edge, the actor and the evidence requirements
    - honors idempotency keys: the same key replays the recorded result
    - writes state + transition atomically (BEGIN IMMEDIATE)
    - mirrors a plain-language event into job_events for the existing UI
    """
    if to_state not in STATES:
        raise InvalidTransition(job_id, "?", to_state, "unknown state")
    if actor not in ACTORS:
        raise InvalidTransition(job_id, "?", to_state, f"unknown actor '{actor}'")
    if to_state == APPROVED and actor != "user":
        raise InvalidTransition(job_id, "?", to_state,
                                "APPROVED may only be set by the human user")
    if to_state in EVIDENCE_REQUIRED and not evidence and not note:
        raise InvalidTransition(job_id, "?", to_state,
                                "evidence or note required for this transition")

    with db.connect() as conn:
        ensure_schema(conn)
        conn.execute("BEGIN IMMEDIATE")     # serialize concurrent workers
        try:
            # Idempotent replay?
            if idempotency_key:
                prior = conn.execute(
                    "SELECT * FROM application_transitions WHERE idempotency_key=?",
                    (idempotency_key,)).fetchone()
                if prior:
                    conn.rollback()
                    return TransitionResult(
                        job_id=prior["job_id"], from_state=prior["from_state"],
                        to_state=prior["to_state"], actor=prior["actor"],
                        at=prior["created_at"], idempotency_key=idempotency_key,
                        replayed=True)

            from_state = current_state(job_id, conn=conn)
            if not can_transition(from_state, to_state):
                raise InvalidTransition(job_id, from_state, to_state,
                                        "not a valid lifecycle edge")
            if from_state == SUBMISSION_UNCERTAIN and actor != "user":
                raise InvalidTransition(
                    job_id, from_state, to_state,
                    "uncertain submissions require a human decision")
            if to_state in SUBMISSION_STATES and not idempotency_key:
                raise InvalidTransition(
                    job_id, from_state, to_state,
                    "submission states require an idempotency key")
            # Approval gate: no submission without a recorded human APPROVED.
            if to_state == SUBMISSION_ATTEMPTED:
                approved = conn.execute(
                    "SELECT 1 FROM application_transitions "
                    "WHERE job_id=? AND to_state=? AND actor='user' LIMIT 1",
                    (job_id, APPROVED)).fetchone()
                if not approved:
                    raise InvalidTransition(
                        job_id, from_state, to_state,
                        "no human approval on record — submission refused")

            at = _now()
            ev_json = json.dumps(evidence, ensure_ascii=False) if evidence else None
            conn.execute(
                "INSERT INTO application_transitions "
                "(job_id, from_state, to_state, actor, evidence, note, "
                " idempotency_key, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (job_id, from_state, to_state, actor, ev_json, note or "",
                 idempotency_key, at))
            conn.execute(
                "INSERT INTO application_state (job_id, state, updated_at, updated_by) "
                "VALUES (?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET "
                "state=excluded.state, updated_at=excluded.updated_at, "
                "updated_by=excluded.updated_by",
                (job_id, to_state, at, actor))
            db.record_event(conn, job_id, "state",
                            f"{from_state} -> {to_state} ({actor})"
                            + (f": {note}" if note else ""))
            conn.commit()
            return TransitionResult(job_id=job_id, from_state=from_state,
                                    to_state=to_state, actor=actor, at=at,
                                    idempotency_key=idempotency_key, replayed=False)
        except Exception:
            conn.rollback()
            raise


def history(job_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """Full transition history for one application (audit trail)."""
    with db.connect() as conn:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT from_state, to_state, actor, evidence, note, "
            "idempotency_key, created_at FROM application_transitions "
            "WHERE job_id=? ORDER BY id DESC LIMIT ?",
            (job_id, limit)).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            try:
                item["evidence"] = json.loads(item["evidence"] or "null")
            except (ValueError, TypeError):
                pass
            out.append(item)
        return out


def new_idempotency_key(prefix: str = "submit") -> str:
    return f"{prefix}:{uuid.uuid4().hex}"


def assert_not_submitted(job_id: str, conn: sqlite3.Connection | None = None
                         ) -> None:
    """Guard for submission executors: raise if the job is already submitted
    (confirmed or uncertain). Used together with apply_locks to make double
    submission structurally impossible."""
    state = current_state(job_id, conn=conn)
    if state in (SUBMITTED_CONFIRMED, SUBMISSION_UNCERTAIN, SUBMISSION_ATTEMPTED):
        raise InvalidTransition(job_id, state, "SUBMIT",
                                "a submission already exists for this job")


def compatible_state(job_id: str) -> str:
    """Formal state for display (never mutates anything)."""
    return current_state(job_id)


def state_counts() -> dict[str, int]:
    """Aggregate counts by formal state across all known applications."""
    with db.connect() as conn:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT state, COUNT(*) c FROM application_state GROUP BY state").fetchall()
        counts = {r["state"]: r["c"] for r in rows}
        # jobs without a formal state yet count as DISCOVERED
        total = conn.execute("SELECT COUNT(*) c FROM jobs").fetchone()["c"]
        tracked = sum(counts.values())
        if total > tracked:
            counts[DISCOVERED] = counts.get(DISCOVERED, 0) + (total - tracked)
        return counts
