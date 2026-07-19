"""Approval engine: single-use decisions, expiry, and binding to a plan version."""

from datetime import UTC, datetime, timedelta

import pytest

from ops_assistant.approval import ApprovalEngine, ApprovalStatus
from ops_assistant.errors import (
    ApprovalAlreadyDecidedError,
    ApprovalExpiredError,
    ApprovalNotFoundError,
    PlanChangedError,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now = self.now + timedelta(**kwargs)


def _counter_ids() -> object:
    n = iter(range(1, 1000))

    def factory() -> str:
        return f"appr-{next(n)}"

    return factory


def _engine(clock: FakeClock | None = None) -> ApprovalEngine:
    return ApprovalEngine(clock=clock or FakeClock(), id_factory=_counter_ids())


def _request(engine: ApprovalEngine, fingerprint: str = "fp-abc") -> str:
    appr = engine.request(
        workflow_id="wf1",
        step_id="s1",
        plan_fingerprint=fingerprint,
        tool="email.send",
        arguments={"to": "a@b.c"},
        risk="external_side_effect",
        ttl=timedelta(hours=1),
    )
    return appr.id


def test_request_creates_a_pending_approval_bound_to_the_plan() -> None:
    engine = _engine()
    appr = engine.request(
        workflow_id="wf1",
        step_id="s1",
        plan_fingerprint="fp-abc",
        tool="email.send",
        arguments={"to": "a@b.c"},
        risk="external_side_effect",
        ttl=timedelta(hours=1),
    )
    assert appr.status is ApprovalStatus.PENDING
    assert appr.plan_fingerprint == "fp-abc"


def test_approve_records_actor_and_reason() -> None:
    engine = _engine()
    aid = _request(engine)
    decided = engine.approve(aid, actor="roman", plan_fingerprint="fp-abc", reason="looks good")
    assert decided.status is ApprovalStatus.APPROVED
    assert decided.decided_by == "roman"
    assert decided.decision_reason == "looks good"


def test_approving_twice_is_rejected_idempotency() -> None:
    engine = _engine()
    aid = _request(engine)
    engine.approve(aid, actor="roman", plan_fingerprint="fp-abc")
    with pytest.raises(ApprovalAlreadyDecidedError):
        engine.approve(aid, actor="roman", plan_fingerprint="fp-abc")


def test_rejected_approval_cannot_then_be_approved() -> None:
    engine = _engine()
    aid = _request(engine)
    engine.reject(aid, actor="roman", plan_fingerprint="fp-abc")
    with pytest.raises(ApprovalAlreadyDecidedError):
        engine.approve(aid, actor="roman", plan_fingerprint="fp-abc")


def test_unknown_approval_raises() -> None:
    with pytest.raises(ApprovalNotFoundError):
        _engine().approve("nope", actor="x", plan_fingerprint="fp-abc")


def test_expired_approval_cannot_be_approved() -> None:
    clock = FakeClock()
    engine = _engine(clock)
    aid = _request(engine)
    clock.advance(hours=2)  # past the 1h ttl
    with pytest.raises(ApprovalExpiredError):
        engine.approve(aid, actor="roman", plan_fingerprint="fp-abc")


def test_changed_plan_invalidates_the_approval() -> None:
    engine = _engine()
    aid = _request(engine, fingerprint="fp-original")
    with pytest.raises(PlanChangedError):
        engine.approve(aid, actor="roman", plan_fingerprint="fp-different")


def test_cancel_marks_a_pending_approval_cancelled() -> None:
    engine = _engine()
    aid = _request(engine)
    cancelled = engine.cancel(aid)
    assert cancelled.status is ApprovalStatus.CANCELLED
    assert engine.pending_for_workflow("wf1") == ()


def test_cancel_is_idempotent_on_a_decided_approval() -> None:
    engine = _engine()
    aid = _request(engine)
    engine.approve(aid, actor="roman", plan_fingerprint="fp-abc")
    # cancelling an already-approved approval leaves it approved, does not raise
    assert engine.cancel(aid).status is ApprovalStatus.APPROVED


def test_compare_and_set_only_writes_when_status_matches() -> None:
    # The atomic swap that closes the cross-process double-decide race: it writes
    # only if the stored status still equals the expected one.
    from ops_assistant.approval import Approval, InMemoryApprovalStore

    store = InMemoryApprovalStore()
    pending = Approval(
        id="a1",
        workflow_id="wf1",
        step_id="s1",
        plan_fingerprint="fp",
        tool="email.send",
        arguments={},
        risk="external_side_effect",
        status=ApprovalStatus.PENDING,
        created_at=datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
        expires_at=datetime(2026, 7, 19, 13, 0, tzinfo=UTC),
    )
    store.add(pending)
    approved = pending.model_copy(update={"status": ApprovalStatus.APPROVED})

    assert store.compare_and_set(approved, expected_status=ApprovalStatus.PENDING) is True
    # Now stored status is APPROVED, so a second swap expecting PENDING is refused.
    assert store.compare_and_set(approved, expected_status=ApprovalStatus.PENDING) is False
    # An unknown id is also refused.
    ghost = pending.model_copy(update={"id": "nope"})
    assert store.compare_and_set(ghost, expected_status=ApprovalStatus.PENDING) is False


def test_pending_for_workflow_lists_open_approvals() -> None:
    engine = _engine()
    _request(engine)
    assert [a.step_id for a in engine.pending_for_workflow("wf1")] == ["s1"]
