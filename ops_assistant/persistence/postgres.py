"""Postgres implementations of the three storage seams.

Same interfaces the in-memory stores satisfy, so `OpsService` is unchanged — only
where state lives changes. Highlights:

* ``PostgresWorkflowStore.save`` does an optimistic update guarded by ``version``;
  a lost race raises :class:`ConflictError` (HTTP 409) instead of clobbering.
* ``PostgresAuditStore.append`` relies on the DB's append-only trigger; the row's
  ``seq`` comes from a Postgres identity column.
* ``PostgresApprovalStore`` persists the approval state machine so a paused,
  awaiting-approval workflow survives a restart.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime

from sqlalchemy import RowMapping, delete, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine

from ops_assistant.approval import Approval, ApprovalStatus
from ops_assistant.audit import AuditEvent, AuditEventType
from ops_assistant.errors import ConflictError
from ops_assistant.gateway import ToolResult
from ops_assistant.models import OperationRequest, StepStatus, WorkflowStatus
from ops_assistant.persistence import schema
from ops_assistant.policy import ValidatedStep
from ops_assistant.state import StepRecord, WorkflowRecord


class PostgresWorkflowStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create(self, workflow: WorkflowRecord) -> None:
        with self._engine.begin() as conn:
            conn.execute(insert(schema.workflows).values(_workflow_row(workflow)))
            if workflow.steps:
                conn.execute(insert(schema.steps), _step_rows(workflow))

    def get(self, workflow_id: str) -> WorkflowRecord | None:
        with self._engine.connect() as conn:
            row = (
                conn.execute(select(schema.workflows).where(schema.workflows.c.id == workflow_id))
                .mappings()
                .first()
            )
            if row is None:
                return None
            step_rows = (
                conn.execute(
                    select(schema.steps)
                    .where(schema.steps.c.workflow_id == workflow_id)
                    .order_by(schema.steps.c.ordinal)
                )
                .mappings()
                .all()
            )
        return _to_workflow(row, step_rows)

    def save(self, workflow: WorkflowRecord) -> None:
        expected = workflow.version
        with self._engine.begin() as conn:
            result = conn.execute(
                update(schema.workflows)
                .where(schema.workflows.c.id == workflow.id)
                .where(schema.workflows.c.version == expected)
                .values(_workflow_row(workflow, version=expected + 1))
            )
            if result.rowcount != 1:
                raise ConflictError(
                    f"workflow {workflow.id} was modified concurrently "
                    f"(expected version {expected})"
                )
            conn.execute(delete(schema.steps).where(schema.steps.c.workflow_id == workflow.id))
            if workflow.steps:
                conn.execute(insert(schema.steps), _step_rows(workflow))
        workflow.version = expected + 1


class PostgresApprovalStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add(self, approval: Approval) -> None:
        with self._engine.begin() as conn:
            conn.execute(insert(schema.approvals).values(_approval_row(approval)))

    def get(self, approval_id: str) -> Approval | None:
        with self._engine.connect() as conn:
            row = (
                conn.execute(select(schema.approvals).where(schema.approvals.c.id == approval_id))
                .mappings()
                .first()
            )
        return _to_approval(row) if row is not None else None

    def update(self, approval: Approval) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                update(schema.approvals)
                .where(schema.approvals.c.id == approval.id)
                .values(_approval_row(approval))
            )

    def compare_and_set(self, approval: Approval, *, expected_status: ApprovalStatus) -> bool:
        with self._engine.begin() as conn:
            result = conn.execute(
                update(schema.approvals)
                .where(schema.approvals.c.id == approval.id)
                .where(schema.approvals.c.status == expected_status.value)
                .values(_approval_row(approval))
            )
        return result.rowcount == 1

    def pending_for_workflow(self, workflow_id: str) -> tuple[Approval, ...]:
        with self._engine.connect() as conn:
            rows = (
                conn.execute(
                    select(schema.approvals)
                    .where(schema.approvals.c.workflow_id == workflow_id)
                    .where(schema.approvals.c.status == ApprovalStatus.PENDING.value)
                    .order_by(schema.approvals.c.created_at)
                )
                .mappings()
                .all()
            )
        return tuple(_to_approval(r) for r in rows)


class PostgresAuditStore:
    def __init__(self, engine: Engine, clock: Callable[[], datetime]) -> None:
        self._engine = engine
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
        with self._engine.begin() as conn:
            seq = conn.execute(
                insert(schema.audit_events)
                .values(
                    workflow_id=workflow_id,
                    step_id=step_id,
                    event_type=event_type.value,
                    actor=actor,
                    timestamp=self._clock(),
                    correlation_id=correlation_id,
                    payload=payload or {},
                )
                .returning(schema.audit_events.c.seq)
            ).scalar_one()
        return self._for_seq(seq)

    def events(self) -> tuple[AuditEvent, ...]:
        with self._engine.connect() as conn:
            rows = (
                conn.execute(select(schema.audit_events).order_by(schema.audit_events.c.seq))
                .mappings()
                .all()
            )
        return tuple(_to_audit_event(r) for r in rows)

    def for_workflow(self, workflow_id: str) -> tuple[AuditEvent, ...]:
        with self._engine.connect() as conn:
            rows = (
                conn.execute(
                    select(schema.audit_events)
                    .where(schema.audit_events.c.workflow_id == workflow_id)
                    .order_by(schema.audit_events.c.seq)
                )
                .mappings()
                .all()
            )
        return tuple(_to_audit_event(r) for r in rows)

    def _for_seq(self, seq: int) -> AuditEvent:
        with self._engine.connect() as conn:
            row = (
                conn.execute(select(schema.audit_events).where(schema.audit_events.c.seq == seq))
                .mappings()
                .one()
            )
        return _to_audit_event(row)


class PostgresIdempotencyStore:
    """Idempotency keyed on ``tool_executions.idempotency_key`` (its primary key).

    A repeated ``put`` is an ``INSERT ... ON CONFLICT DO NOTHING`` — the second
    call is a no-op at the database level, so a retried or duplicated execution
    cannot record a second effect even across processes.
    """

    def __init__(self, engine: Engine, clock: Callable[[], datetime]) -> None:
        self._engine = engine
        self._clock = clock

    def get(self, key: str) -> ToolResult | None:
        with self._engine.connect() as conn:
            row = (
                conn.execute(
                    select(schema.tool_executions).where(
                        schema.tool_executions.c.idempotency_key == key
                    )
                )
                .mappings()
                .first()
            )
        if row is None:
            return None
        return ToolResult(tool=row["tool"], output=row["output"], replayed=True)

    def put(self, key: str, *, workflow_id: str, step_id: str, result: ToolResult) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                pg_insert(schema.tool_executions)
                .values(
                    idempotency_key=key,
                    workflow_id=workflow_id,
                    step_id=step_id,
                    tool=result.tool,
                    output=result.output,
                    created_at=self._clock(),
                )
                .on_conflict_do_nothing(index_elements=["idempotency_key"])
            )


# --- Row <-> record mapping ---


def _workflow_row(wf: WorkflowRecord, *, version: int | None = None) -> dict[str, object]:
    return {
        "id": wf.id,
        "status": wf.status.value,
        "summary": wf.summary,
        "requires_clarification": wf.requires_clarification,
        "clarification_question": wf.clarification_question,
        "plan_fingerprint": wf.plan_fingerprint,
        "version": wf.version if version is None else version,
        "request": wf.request.model_dump(mode="json"),
    }


def _step_rows(wf: WorkflowRecord) -> list[dict[str, object]]:
    return [
        {
            "workflow_id": wf.id,
            "step_id": step.validated.id,
            "ordinal": ordinal,
            "validated": step.validated.model_dump(mode="json"),
            "status": step.status.value,
            "output": step.output,
            "approval_id": step.approval_id,
            "error": step.error,
        }
        for ordinal, step in enumerate(wf.steps)
    ]


def _to_workflow(row: RowMapping, step_rows: Sequence[RowMapping]) -> WorkflowRecord:
    return WorkflowRecord(
        id=row["id"],
        request=OperationRequest.model_validate(row["request"]),
        status=WorkflowStatus(row["status"]),
        summary=row["summary"],
        requires_clarification=row["requires_clarification"],
        clarification_question=row["clarification_question"],
        plan_fingerprint=row["plan_fingerprint"],
        version=row["version"],
        steps=[
            StepRecord(
                validated=ValidatedStep.model_validate(sr["validated"]),
                status=StepStatus(sr["status"]),
                output=sr["output"],
                approval_id=sr["approval_id"],
                error=sr["error"],
            )
            for sr in step_rows
        ],
    )


def _approval_row(a: Approval) -> dict[str, object]:
    return {
        "id": a.id,
        "workflow_id": a.workflow_id,
        "step_id": a.step_id,
        "plan_fingerprint": a.plan_fingerprint,
        "tool": a.tool,
        "arguments": a.arguments,
        "risk": a.risk,
        "status": a.status.value,
        "created_at": a.created_at,
        "expires_at": a.expires_at,
        "decided_by": a.decided_by,
        "decided_at": a.decided_at,
        "decision_reason": a.decision_reason,
    }


def _to_approval(row: RowMapping) -> Approval:
    return Approval.model_validate({**row, "status": ApprovalStatus(row["status"])})


def _to_audit_event(row: RowMapping) -> AuditEvent:
    return AuditEvent.model_validate({**row, "event_type": AuditEventType(row["event_type"])})
