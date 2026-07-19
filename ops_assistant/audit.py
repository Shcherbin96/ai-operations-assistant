"""Append-only audit trail.

Every meaningful decision — request received, plan generated, risk mismatch
detected, approval requested/decided, tool called, workflow completed — becomes an
immutable :class:`AuditEvent`. The log exposes only *append* and *read*: there is
no update or delete, and the read view is an immutable tuple, so history cannot be
rewritten through this interface.

In Stage 1 this is an in-memory list. Stage 2 backs the same interface with a
Postgres table whose append-only property is enforced by GRANTs and triggers.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class AuditEventType(StrEnum):
    REQUEST_CREATED = "request.created"
    PLAN_GENERATED = "plan.generated"
    PLAN_VALIDATED = "plan.validated"
    RISK_MISMATCH_DETECTED = "plan.risk_mismatch_detected"
    STEP_STARTED = "step.started"
    STEP_SUCCEEDED = "step.succeeded"
    STEP_FAILED = "step.failed"
    STEP_SKIPPED = "step.skipped"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_APPROVED = "approval.approved"
    APPROVAL_REJECTED = "approval.rejected"
    TOOL_CALLED = "tool.called"
    TOOL_SUCCEEDED = "tool.succeeded"
    TOOL_FAILED = "tool.failed"
    WORKFLOW_COMPLETED = "workflow.completed"
    WORKFLOW_FAILED = "workflow.failed"
    WORKFLOW_CANCELLED = "workflow.cancelled"


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AuditEvent(BaseModel):
    """One immutable record. ``seq`` is a monotonic, log-assigned ordinal."""

    model_config = ConfigDict(frozen=True)

    seq: int
    workflow_id: str
    event_type: AuditEventType
    actor: str
    timestamp: datetime
    step_id: str | None = None
    correlation_id: str | None = None
    payload: dict[str, object] = Field(default_factory=dict)


class AuditLog:
    def __init__(self, clock: Callable[[], datetime] = _utcnow) -> None:
        self._events: list[AuditEvent] = []
        self._clock = clock

    def append(
        self,
        workflow_id: str,
        event_type: AuditEventType,
        *,
        actor: str,
        step_id: str | None = None,
        correlation_id: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            seq=len(self._events) + 1,
            workflow_id=workflow_id,
            event_type=event_type,
            actor=actor,
            timestamp=self._clock(),
            step_id=step_id,
            correlation_id=correlation_id,
            payload=payload or {},
        )
        self._events.append(event)
        return event

    def events(self) -> tuple[AuditEvent, ...]:
        return tuple(self._events)

    def for_workflow(self, workflow_id: str) -> tuple[AuditEvent, ...]:
        return tuple(e for e in self._events if e.workflow_id == workflow_id)
