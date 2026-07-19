"""The approval engine: human-in-the-loop, made safe.

Three properties matter here and each is a test:

* **Single-use** — an approval can be decided exactly once. Tapping *Approve*
  twice cannot send an email twice.
* **Expiring** — an approval past its TTL is dead; deciding it raises.
* **Plan-bound** — an approval carries the fingerprint of the plan it was issued
  against. If the plan changed since, the old approval is invalid, so a user can
  never unknowingly approve actions they never saw.

The engine holds the *logic*; where approvals are stored is an
:class:`ApprovalStore` (in-memory here, Postgres in Stage 2).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from ops_assistant.errors import (
    ApprovalAlreadyDecidedError,
    ApprovalExpiredError,
    ApprovalNotFoundError,
    PlanChangedError,
)


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class Approval(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    workflow_id: str
    step_id: str
    plan_fingerprint: str
    tool: str
    arguments: dict[str, object]
    risk: str
    status: ApprovalStatus
    created_at: datetime
    expires_at: datetime
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_reason: str | None = None


class ApprovalStore(Protocol):
    def add(self, approval: Approval) -> None: ...

    def get(self, approval_id: str) -> Approval | None: ...

    def update(self, approval: Approval) -> None: ...

    def pending_for_workflow(self, workflow_id: str) -> tuple[Approval, ...]: ...


class InMemoryApprovalStore:
    def __init__(self) -> None:
        self._approvals: dict[str, Approval] = {}

    def add(self, approval: Approval) -> None:
        self._approvals[approval.id] = approval

    def get(self, approval_id: str) -> Approval | None:
        return self._approvals.get(approval_id)

    def update(self, approval: Approval) -> None:
        self._approvals[approval.id] = approval

    def pending_for_workflow(self, workflow_id: str) -> tuple[Approval, ...]:
        return tuple(
            a
            for a in self._approvals.values()
            if a.workflow_id == workflow_id and a.status is ApprovalStatus.PENDING
        )


def _uuid_hex() -> str:  # pragma: no cover - trivial default
    import uuid

    return uuid.uuid4().hex


class ApprovalEngine:
    def __init__(
        self,
        clock: Callable[[], datetime],
        id_factory: Callable[[], str] = _uuid_hex,
        store: ApprovalStore | None = None,
    ) -> None:
        self._clock = clock
        self._id_factory = id_factory
        self._store: ApprovalStore = store or InMemoryApprovalStore()

    def request(
        self,
        *,
        workflow_id: str,
        step_id: str,
        plan_fingerprint: str,
        tool: str,
        arguments: dict[str, object],
        risk: str,
        ttl: timedelta,
    ) -> Approval:
        now = self._clock()
        approval = Approval(
            id=self._id_factory(),
            workflow_id=workflow_id,
            step_id=step_id,
            plan_fingerprint=plan_fingerprint,
            tool=tool,
            arguments=dict(arguments),
            risk=risk,
            status=ApprovalStatus.PENDING,
            created_at=now,
            expires_at=now + ttl,
        )
        self._store.add(approval)
        return approval

    def get(self, approval_id: str) -> Approval:
        approval = self._store.get(approval_id)
        if approval is None:
            raise ApprovalNotFoundError(f"no such approval: {approval_id}")
        return approval

    def approve(
        self, approval_id: str, *, actor: str, plan_fingerprint: str, reason: str | None = None
    ) -> Approval:
        return self._decide(approval_id, ApprovalStatus.APPROVED, actor, plan_fingerprint, reason)

    def reject(
        self, approval_id: str, *, actor: str, plan_fingerprint: str, reason: str | None = None
    ) -> Approval:
        return self._decide(approval_id, ApprovalStatus.REJECTED, actor, plan_fingerprint, reason)

    def cancel(self, approval_id: str) -> Approval:
        """Void a still-pending approval (e.g. a sibling of a rejected step).

        Idempotent: an already-decided approval is returned unchanged.
        """
        approval = self.get(approval_id)
        if approval.status is ApprovalStatus.PENDING:
            approval = approval.model_copy(update={"status": ApprovalStatus.CANCELLED})
            self._store.update(approval)
        return approval

    def pending_for_workflow(self, workflow_id: str) -> tuple[Approval, ...]:
        return self._store.pending_for_workflow(workflow_id)

    def _decide(
        self,
        approval_id: str,
        outcome: ApprovalStatus,
        actor: str,
        plan_fingerprint: str,
        reason: str | None,
    ) -> Approval:
        approval = self.get(approval_id)

        if approval.status is not ApprovalStatus.PENDING:
            raise ApprovalAlreadyDecidedError(
                f"approval {approval_id} already {approval.status.value}"
            )

        now = self._clock()
        if now > approval.expires_at:
            self._store.update(approval.model_copy(update={"status": ApprovalStatus.EXPIRED}))
            raise ApprovalExpiredError(f"approval {approval_id} expired")

        if plan_fingerprint != approval.plan_fingerprint:
            raise PlanChangedError(
                f"approval {approval_id} was issued against a different plan version"
            )

        decided = approval.model_copy(
            update={
                "status": outcome,
                "decided_by": actor,
                "decided_at": now,
                "decision_reason": reason,
            }
        )
        self._store.update(decided)
        return decided
