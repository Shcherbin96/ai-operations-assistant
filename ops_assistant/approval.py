"""The approval engine: human-in-the-loop, made safe.

Three properties matter here and each is a test:

* **Single-use** — an approval can be decided exactly once. Tapping *Approve*
  twice cannot send an email twice.
* **Expiring** — an approval past its TTL is dead; deciding it raises.
* **Plan-bound** — an approval carries the fingerprint of the plan it was issued
  against. If the plan changed since, the old approval is invalid, so a user can
  never unknowingly approve actions they never saw.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from enum import StrEnum

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


def _uuid_hex() -> str:  # pragma: no cover - trivial default
    import uuid

    return uuid.uuid4().hex


class ApprovalEngine:
    def __init__(
        self,
        clock: Callable[[], datetime],
        id_factory: Callable[[], str] = _uuid_hex,
    ) -> None:
        self._clock = clock
        self._id_factory = id_factory
        self._approvals: dict[str, Approval] = {}

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
        self._approvals[approval.id] = approval
        return approval

    def get(self, approval_id: str) -> Approval:
        approval = self._approvals.get(approval_id)
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

    def pending_for_workflow(self, workflow_id: str) -> tuple[Approval, ...]:
        return tuple(
            a
            for a in self._approvals.values()
            if a.workflow_id == workflow_id and a.status is ApprovalStatus.PENDING
        )

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
            self._approvals[approval_id] = approval.model_copy(
                update={"status": ApprovalStatus.EXPIRED}
            )
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
        self._approvals[approval_id] = decided
        return decided
