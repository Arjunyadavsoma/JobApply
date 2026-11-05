"""Phase 19D — application lifecycle state machine tests."""
from __future__ import annotations

import pytest

from jobagent import db
from jobagent import state_machine as sm

# String constants are safe to import (immutable); the exception class is
# referenced as sm.InvalidTransition because conftest reloads modules per test.
from jobagent.state_machine import (APPROVED, AWAITING_USER, DISCOVERED,
                                    ELIGIBLE, FAILED, IN_PROGRESS,
                                    PREPARATION_PENDING, PREPARING,
                                    READY_FOR_REVIEW, REJECTED,
                                    SUBMISSION_ATTEMPTED, SUBMISSION_UNCERTAIN,
                                    SUBMITTED_CONFIRMED, VERIFIED_OPEN,
                                    WITHDRAWN)


@pytest.fixture()
def job(env):
    """One job row to walk through the lifecycle."""
    job_id = "sm-test-1"
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO jobs (id, source, company, title, url, apply_url,"
            " description, posted_at, discovered_at, updated_at, status) VALUES "
            "(?,?,?,?,?,?,?,?,?,?,?)",
            (job_id, "test", "Acme", "SDE", "https://acme.test/j", "https://acme.test/j",
             "d", "2026-11-01", "2026-11-04", "2026-11-04", "new"))
        conn.commit()
    return job_id


def _walk(job_id, steps):
    """Apply a list of (to_state, actor, kwargs) transitions."""
    for to_state, actor, kw in steps:
        sm.transition(job_id, to_state, actor=actor, **kw)


class TestLifecycleGraph:
    def test_happy_path(self, job):
        steps = [
            (sm.VERIFICATION_PENDING, "system", {"note": "queued"}),
            (sm.VERIFIED_OPEN, "system", {"note": "HTTP 200"}),
            (sm.ELIGIBILITY_PENDING, "system", {"note": "start"}),
            (sm.ELIGIBLE, "agent", {"note": "2027 batch OK"}),
            (sm.PREPARATION_PENDING, "system", {"note": "start prep"}),
            (sm.PREPARING, "agent", {"note": "tailoring"}),
            (sm.READY_FOR_REVIEW, "agent", {"note": "preview built"}),
            (sm.APPROVED, "user", {"note": "I approve this application"}),
            (sm.IN_PROGRESS, "agent", {"note": "browser session open"}),
            (sm.SUBMISSION_ATTEMPTED, "agent",
             {"evidence": {"url": "https://acme.test/confirm"},
              "idempotency_key": "submit:test1"}),
            (sm.SUBMITTED_CONFIRMED, "user",
             {"evidence": {"confirmation": "REF-1234"},
              "idempotency_key": "confirm:test1"}),
        ]
        _walk(job, steps)
        assert sm.current_state(job) == SUBMITTED_CONFIRMED

    def test_invalid_edge_rejected(self, job):
        with pytest.raises(sm.InvalidTransition):
            sm.transition(job, sm.SUBMITTED_CONFIRMED, actor="agent",
                          evidence={"x": 1}, idempotency_key="k1")

    def test_unknown_state_rejected(self, job):
        with pytest.raises(sm.InvalidTransition):
            sm.transition(job, "NOT_A_STATE", actor="system")

    def test_unknown_actor_rejected(self, job):
        with pytest.raises(sm.InvalidTransition):
            sm.transition(job, sm.VERIFICATION_PENDING, actor="attacker")

    def test_evidence_required_for_terminal_decisions(self, job):
        # REJECTED / FAILED / APPROVED / submission states need evidence or note
        with pytest.raises(sm.InvalidTransition):
            sm.transition(job, sm.REJECTED, actor="agent")  # no evidence/note
        with pytest.raises(sm.InvalidTransition):
            sm.transition(job, sm.APPROVED, actor="user")   # approval needs a note
        # ...but ordinary progress states do not require evidence
        sm.transition(job, sm.VERIFICATION_PENDING, actor="system")
        assert sm.current_state(job) == sm.VERIFICATION_PENDING


class TestHumanGates:
    def test_approval_requires_human(self, job):
        sm.transition(job, sm.VERIFICATION_PENDING, actor="system", note="q")
        sm.transition(job, sm.VERIFIED_OPEN, actor="system", note="ok")
        sm.transition(job, sm.ELIGIBILITY_PENDING, actor="system", note="q")
        sm.transition(job, sm.ELIGIBLE, actor="agent", note="fits")
        sm.transition(job, sm.PREPARATION_PENDING, actor="system", note="q")
        sm.transition(job, sm.PREPARING, actor="agent", note="resume")
        sm.transition(job, sm.READY_FOR_REVIEW, actor="agent", note="ready")
        with pytest.raises(sm.InvalidTransition) as e:
            sm.transition(job, sm.APPROVED, actor="agent", note="auto-approve")
        assert "human" in str(e.value)
        # the user CAN approve
        sm.transition(job, sm.APPROVED, actor="user", note="approved by me")
        assert sm.current_state(job) == APPROVED

    def test_uncertain_never_auto_retries(self, job):
        _reach_in_progress(job)
        sm.transition(job, sm.SUBMISSION_ATTEMPTED, actor="agent",
                      evidence={"url": "https://acme.test/x"},
                      idempotency_key="sub-key-1")
        sm.transition(job, sm.SUBMISSION_UNCERTAIN, actor="agent",
                      evidence={"reason": "no confirmation page"},
                      idempotency_key="unc-key-1")
        assert sm.current_state(job) == SUBMISSION_UNCERTAIN
        # System cannot move it on — not even "back to preparing"
        with pytest.raises(sm.InvalidTransition) as e:
            sm.transition(job, sm.PREPARING, actor="system", note="retry")
        assert "rejected" in str(e.value)  # invalid edge AND human-gate both block
        with pytest.raises(sm.InvalidTransition):
            sm.transition(job, sm.SUBMITTED_CONFIRMED, actor="agent",
                          evidence={"fake": "yes"}, idempotency_key="k2")
        # Only the user's verdict can move it
        sm.transition(job, sm.SUBMITTED_CONFIRMED, actor="user",
                      evidence={"ref": "user checked email, REF-9"},
                      idempotency_key="k3")
        assert sm.current_state(job) == SUBMITTED_CONFIRMED

    def test_confirmed_is_terminal(self, job):
        _reach_in_progress(job)
        _submit_and_confirm(job)
        # No transition leaves SUBMITTED_CONFIRMED
        for target in (sm.PREPARING, sm.IN_PROGRESS, sm.WITHDRAWN,
                       sm.SUBMISSION_ATTEMPTED, sm.FAILED):
            with pytest.raises(sm.InvalidTransition):
                sm.transition(job, target, actor="user", note="no",
                              idempotency_key=f"t-{target}")

    def test_failed_retries_go_back_through_preparation(self, job):
        _reach_in_progress(job)
        sm.transition(job, sm.FAILED, actor="agent",
                      evidence={"error": "form changed"}, idempotency_key="f1")
        assert sm.current_state(job) == FAILED
        # Cannot jump straight back into IN_PROGRESS from FAILED
        with pytest.raises(sm.InvalidTransition):
            sm.transition(job, sm.IN_PROGRESS, actor="agent", note="again")
        sm.transition(job, sm.PREPARING, actor="agent", note="re-prepare")
        assert sm.current_state(job) == PREPARING


class TestIdempotency:
    def test_same_key_replays_without_side_effect(self, job):
        _reach_in_progress(job)
        key = sm.new_idempotency_key("submit")
        r1 = sm.transition(job, sm.SUBMISSION_ATTEMPTED, actor="agent",
                           evidence={"url": "u"}, idempotency_key=key)
        assert r1.replayed is False
        r2 = sm.transition(job, sm.SUBMISSION_ATTEMPTED, actor="agent",
                           evidence={"url": "u"}, idempotency_key=key)
        assert r2.replayed is True
        assert r2.to_state == r1.to_state
        # History records the transition exactly once
        attempts = [h for h in sm.history(job)
                    if h["to_state"] == sm.SUBMISSION_ATTEMPTED]
        assert len(attempts) == 1

    def test_submission_states_require_key(self, job):
        _reach_in_progress(job)
        with pytest.raises(sm.InvalidTransition) as e:
            sm.transition(job, sm.SUBMISSION_ATTEMPTED, actor="agent",
                          evidence={"url": "u"})
        assert "idempotency" in str(e.value)

    def test_assert_not_submitted_guard(self, job):
        _reach_in_progress(job)
        _submit_and_confirm(job)
        with pytest.raises(sm.InvalidTransition) as e:
            sm.assert_not_submitted(job)
        assert "already exists" in str(e.value)


class TestAuditTrail:
    def test_history_records_everything(self, job):
        _reach_in_progress(job)
        hist = sm.history(job)
        assert len(hist) >= 8
        entry = hist[0]  # newest first
        assert entry["from_state"] and entry["to_state"]
        assert entry["actor"] in ("system", "user", "agent")
        assert entry["created_at"]  # timestamp always present

    def test_transition_mirrors_into_job_events(self, job):
        _reach_in_progress(job)
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT text FROM job_events WHERE job_id=? AND kind='state'",
                (job,)).fetchall()
        assert rows, "state transitions must appear in job_events timeline"

    def test_legacy_queue_state_seeds_current_state(self, job):
        with db.connect() as conn:
            conn.execute("UPDATE jobs SET queue_state='awaiting_approval' WHERE id=?",
                         (job,))
            conn.commit()
        assert sm.current_state(job) == READY_FOR_REVIEW

    def test_state_counts_shape(self, job):
        counts = sm.state_counts()
        assert sm.DISCOVERED in counts
        assert sum(counts.values()) >= 1


class TestConcurrency:
    def test_concurrent_transitions_serialize(self, job):
        """Two threads racing transitions: only one wins, no corruption."""
        import threading
        sm.transition(job, sm.VERIFICATION_PENDING, actor="system", note="q")
        sm.transition(job, sm.VERIFIED_OPEN, actor="system", note="ok")
        sm.transition(job, sm.ELIGIBILITY_PENDING, actor="system", note="q")
        errors: list[Exception] = []
        results = []

        def worker(target):
            try:
                results.append(sm.transition(job, target, actor="agent", note="race"))
            except sm.InvalidTransition as exc:
                errors.append(exc)

        t1 = threading.Thread(target=worker, args=(sm.ELIGIBLE,))
        t2 = threading.Thread(target=worker, args=(sm.REJECTED,))
        t1.start(); t2.start(); t1.join(); t2.join()
        # Exactly one succeeded (both start from ELIGIBILITY_PENDING).
        assert len(results) == 1
        assert len(errors) == 1
        # State is consistent with whichever won.
        final = sm.current_state(job)
        assert final in (sm.ELIGIBLE, sm.REJECTED)


# ---------------------------------------------------------------- helpers

def agent_note_helper() -> str:
    return "agent"


def _reach_in_progress(job_id: str) -> None:
    _walk(job_id, [
        (sm.VERIFICATION_PENDING, "system", {"note": "queued"}),
        (sm.VERIFIED_OPEN, "system", {"note": "HTTP 200"}),
        (sm.ELIGIBILITY_PENDING, "system", {"note": "start"}),
        (sm.ELIGIBLE, "agent", {"note": "fits"}),
        (sm.PREPARATION_PENDING, "system", {"note": "start prep"}),
        (sm.PREPARING, "agent", {"note": "tailoring"}),
        (sm.READY_FOR_REVIEW, "agent", {"note": "preview built"}),
        (sm.APPROVED, "user", {"note": "approved"}),
        (sm.IN_PROGRESS, "agent", {"note": "session open"}),
    ])


def _submit_and_confirm(job_id: str) -> None:
    sm.transition(job_id, sm.SUBMISSION_ATTEMPTED, actor="agent",
                  evidence={"url": "https://acme.test/confirm"},
                  idempotency_key=f"submit:{job_id}")
    sm.transition(job_id, sm.SUBMITTED_CONFIRMED, actor="user",
                  evidence={"confirmation": "REF-1"},
                  idempotency_key=f"confirm:{job_id}")


class TestApprovalGate:
    """19D hardening: no submission without a recorded human approval."""

    def test_submission_blocked_without_approval_record(self, job):
        """The graph itself only allows IN_PROGRESS via APPROVED; the DB-level
        approval check is defense-in-depth against corrupted/forged state.
        Simulate a forged IN_PROGRESS with no APPROVED history."""
        # direct jump from READY_FOR_REVIEW to IN_PROGRESS is graph-impossible
        _walk(job, [
            (sm.VERIFICATION_PENDING, "system", {"note": "q"}),
            (sm.VERIFIED_OPEN, "system", {"note": "ok"}),
            (sm.ELIGIBILITY_PENDING, "system", {"note": "q"}),
            (sm.ELIGIBLE, "agent", {"note": "fits"}),
            (sm.PREPARATION_PENDING, "system", {"note": "q"}),
            (sm.PREPARING, "agent", {"note": "tailor"}),
            (sm.READY_FOR_REVIEW, "agent", {"note": "preview"}),
        ])
        with pytest.raises(sm.InvalidTransition):
            sm.transition(job, sm.IN_PROGRESS, actor="agent", note="jump")
        # forge an IN_PROGRESS state directly in the DB (corruption scenario)
        with db.connect() as conn:
            sm.ensure_schema(conn)
            conn.execute(
                "INSERT INTO application_state (job_id, state, updated_at, updated_by) "
                "VALUES (?,?,?,?) ON CONFLICT(job_id) DO UPDATE SET state=excluded.state",
                (job, sm.IN_PROGRESS, "2026-01-01T00:00:00+00:00", "attacker"))
            conn.commit()
        with pytest.raises(sm.InvalidTransition) as e:
            sm.transition(job, sm.SUBMISSION_ATTEMPTED, actor="agent",
                          evidence={"url": "u"}, idempotency_key="gate-1")
        assert "no human approval" in str(e.value)
        # the user approves explicitly (from the forged IN_PROGRESS there is no
        # valid path back, so re-forge to READY_FOR_REVIEW first, then approve)
        with db.connect() as conn:
            conn.execute("UPDATE application_state SET state=? WHERE job_id=?",
                         (sm.READY_FOR_REVIEW, job))
            conn.commit()
        sm.transition(job, sm.APPROVED, actor="user", note="final approval")
        sm.transition(job, sm.IN_PROGRESS, actor="agent", note="session open")
        sm.transition(job, sm.SUBMISSION_ATTEMPTED, actor="agent",
                      evidence={"url": "u"}, idempotency_key="gate-2")
        assert sm.current_state(job) == sm.SUBMISSION_ATTEMPTED

    def test_awaiting_user_returns_to_review(self, job):
        _walk(job, [
            (sm.VERIFICATION_PENDING, "system", {"note": "q"}),
            (sm.VERIFIED_OPEN, "system", {"note": "ok"}),
            (sm.ELIGIBILITY_PENDING, "system", {"note": "q"}),
            (sm.ELIGIBLE, "agent", {"note": "fits"}),
            (sm.PREPARATION_PENDING, "system", {"note": "q"}),
            (sm.PREPARING, "agent", {"note": "tailor"}),
            (sm.AWAITING_USER, "agent", {"note": "question"}),
        ])
        sm.transition(job, sm.READY_FOR_REVIEW, actor="user", note="resolved")
        assert sm.current_state(job) == sm.READY_FOR_REVIEW
