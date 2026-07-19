"""Append-only audit log: monotonic, ordered, immutable, and never mutated in place."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ops_assistant.audit import AuditEventType, AuditLog


def _fixed_clock() -> object:
    ticks = iter(datetime(2026, 7, 19, 12, 0, s, tzinfo=UTC) for s in range(60))

    def clock() -> datetime:
        return next(ticks)

    return clock


def test_append_returns_monotonic_sequence() -> None:
    log = AuditLog(clock=_fixed_clock())
    e1 = log.append("wf1", AuditEventType.REQUEST_CREATED, actor="u1")
    e2 = log.append("wf1", AuditEventType.PLAN_GENERATED, actor="planner")
    assert e1.seq == 1
    assert e2.seq == 2


def test_events_are_returned_in_order() -> None:
    log = AuditLog(clock=_fixed_clock())
    log.append("wf1", AuditEventType.REQUEST_CREATED, actor="u1")
    log.append("wf1", AuditEventType.WORKFLOW_COMPLETED, actor="system")
    types = [e.event_type for e in log.events()]
    assert types == [AuditEventType.REQUEST_CREATED, AuditEventType.WORKFLOW_COMPLETED]


def test_for_workflow_filters() -> None:
    log = AuditLog(clock=_fixed_clock())
    log.append("wf1", AuditEventType.REQUEST_CREATED, actor="u1")
    log.append("wf2", AuditEventType.REQUEST_CREATED, actor="u2")
    assert [e.workflow_id for e in log.for_workflow("wf2")] == ["wf2"]


def test_timestamp_comes_from_injected_clock() -> None:
    log = AuditLog(clock=_fixed_clock())
    e = log.append("wf1", AuditEventType.REQUEST_CREATED, actor="u1")
    assert e.timestamp == datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)


def test_payload_is_recorded() -> None:
    log = AuditLog(clock=_fixed_clock())
    e = log.append(
        "wf1", AuditEventType.TOOL_CALLED, actor="gateway", payload={"tool": "email.search"}
    )
    assert e.payload == {"tool": "email.search"}


def test_events_are_immutable() -> None:
    log = AuditLog(clock=_fixed_clock())
    e = log.append("wf1", AuditEventType.REQUEST_CREATED, actor="u1")
    with pytest.raises(ValidationError):
        e.actor = "someone else"  # type: ignore[misc]


def test_events_view_is_an_immutable_snapshot() -> None:
    # The read view is a tuple: callers cannot append to it and thereby forge
    # history, and it is a snapshot, not a live handle to internal state.
    log = AuditLog(clock=_fixed_clock())
    log.append("wf1", AuditEventType.REQUEST_CREATED, actor="u1")
    snapshot = log.events()
    assert isinstance(snapshot, tuple)
    log.append("wf1", AuditEventType.WORKFLOW_COMPLETED, actor="system")
    assert len(snapshot) == 1  # earlier snapshot unaffected
    assert len(log.events()) == 2
