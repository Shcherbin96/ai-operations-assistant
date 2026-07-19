"""The orchestrator — where the pieces become a workflow.

``submit`` plans, validates, auto-runs the safe steps, and gates the rest.
``approve`` / ``reject`` resume a paused workflow. Every wiring choice here serves
one rule: nothing with an external side-effect runs until a human says so, and the
model's opinion about risk never changes that.

Stage 1 keeps all state in memory; Stage 2 swaps these dicts for Postgres behind
the same service interface.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict

from ops_assistant.approval import Approval, ApprovalEngine, ApprovalStore
from ops_assistant.audit import AuditEvent, AuditEventType, AuditLog, AuditStore
from ops_assistant.dataflow import resolve_references
from ops_assistant.errors import NotFoundError, OpsAssistantError, StateTransitionError
from ops_assistant.gateway import IdempotencyStore, ToolGateway
from ops_assistant.models import (
    OperationRequest,
    RiskTier,
    StepStatus,
    WorkflowStatus,
    plan_fingerprint,
)
from ops_assistant.planner.base import Planner
from ops_assistant.planner.demo import DemoPlanner
from ops_assistant.policy import ApprovalDecision, PolicyConfig, PolicyEngine
from ops_assistant.state import StepRecord as _Step
from ops_assistant.state import WorkflowRecord as _Workflow
from ops_assistant.state import is_empty as _is_empty
from ops_assistant.store import InMemoryWorkflowStore, WorkflowStore
from ops_assistant.tools.registry import ToolRegistry
from ops_assistant.tools.sandbox import build_sandbox_registry
from ops_assistant.workflow import assert_step_transition, assert_workflow_transition


def _utcnow() -> datetime:
    return datetime.now(UTC)


# --- Audit redaction ---------------------------------------------------------
# The audit trail is append-only: a row written here can never be scrubbed, so it
# errs toward revealing less. It records the *forensic* fields — enough to answer
# "who did this send actually go to?" — while never persisting a message body.
#
# Sensitivity follows *provenance*: an argument whose value was substituted from a
# ``{{step.field}}`` reference carries tool output (an email, a document) and is
# reduced to a shape summary — except routing fields (the recipient IS the point).
# A literal, plan-authored value is the model's own text and is kept (capped).
# Body-like keys are always redacted, at any type. The Telegram approval preview
# uses a separate, non-redacting formatter: informed consent needs the real body.

_ALWAYS_REDACT_KEYS = frozenset({"body", "text", "html", "content"})
_ROUTING_KEYS = frozenset({"to", "cc", "bcc", "recipient", "recipients"})
_MAX_AUDIT_STR = 200


def _summarize(value: object) -> object:
    """A shape-only marker that reveals no content."""
    if isinstance(value, str):
        return f"<redacted {len(value)} chars>"
    if isinstance(value, Mapping):
        return {"redacted_keys": sorted(str(k) for k in value)}
    if isinstance(value, (list, tuple)):
        return {"redacted_items": len(value)}
    return value


def _cap(value: object) -> object:
    """Keep a scalar (capping a long string); never dump a nested structure."""
    if isinstance(value, str) and len(value) > _MAX_AUDIT_STR:
        return value[:_MAX_AUDIT_STR] + f"… (+{len(value) - _MAX_AUDIT_STR} chars)"
    if isinstance(value, (Mapping, list, tuple)):
        return _summarize(value)
    return value


def _redact_for_audit(
    template: Mapping[str, object], resolved: Mapping[str, object]
) -> dict[str, object]:
    """Redact ``resolved`` arguments for the immutable log, using ``template`` to
    tell reference-substituted (sensitive) values from literal plan text."""
    redacted: dict[str, object] = {}
    for key, value in resolved.items():
        substituted = template.get(key) != value
        sensitive = key in _ALWAYS_REDACT_KEYS or (substituted and key not in _ROUTING_KEYS)
        redacted[key] = _summarize(value) if sensitive else _cap(value)
    return redacted


def _result_digest(output: object) -> object:
    """A compact, redacted summary of a tool result for the audit log."""
    if isinstance(output, list):
        return {"count": len(output)}
    if isinstance(output, Mapping):
        return {
            str(key): (_summarize(value) if key in _ALWAYS_REDACT_KEYS else _cap(value))
            for key, value in output.items()
        }
    return _cap(output)


# --- Views: the read models handed back to callers and the API. ---


class StepView(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    tool: str
    arguments: dict[str, Any]
    depends_on: list[str]
    resolved_risk: RiskTier
    decision: ApprovalDecision
    status: StepStatus
    risk_mismatch: bool
    approval_id: str | None
    output: Any | None
    error: str | None


class ApprovalView(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    step_id: str
    tool: str
    arguments: dict[str, Any]
    risk: str


class WorkflowView(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    status: WorkflowStatus
    summary: str
    requires_clarification: bool
    clarification_question: str | None
    steps: list[StepView]
    pending_approvals: list[ApprovalView]


class OpsService:
    def __init__(
        self,
        planner: Planner | None = None,
        registry: ToolRegistry | None = None,
        policy_config: PolicyConfig | None = None,
        clock: Callable[[], datetime] = _utcnow,
        id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
        approval_ttl: timedelta = timedelta(hours=1),
        store: WorkflowStore | None = None,
        approval_store: ApprovalStore | None = None,
        audit_store: AuditStore | None = None,
        idempotency_store: IdempotencyStore | None = None,
        allowed_tools: frozenset[str] | None = None,
    ) -> None:
        self._planner = planner or DemoPlanner()
        self._registry = registry or build_sandbox_registry()
        self._policy = PolicyEngine(self._registry, policy_config or PolicyConfig())
        self._allowed_tools = allowed_tools
        self._audit: AuditStore = audit_store or AuditLog(clock=clock)
        self._approvals = ApprovalEngine(clock=clock, id_factory=id_factory, store=approval_store)
        self._gateway = ToolGateway(self._registry, self._audit, idempotency_store)
        self._id_factory = id_factory
        self._ttl = approval_ttl
        self._workflows: WorkflowStore = store or InMemoryWorkflowStore()
        # Sync endpoints run in a threadpool; this lock serializes state mutations
        # within the process. Across processes, correctness rests on the database:
        # workflow writes use optimistic version locking (ConflictError) and
        # approval decisions are a compare-and-set.
        #
        # Deliberate trade-off (see docs/adr/0005-single-process-locking.md): it is
        # ONE lock, held across planning (LLM) and execution (tool I/O), so unrelated
        # workflows serialize behind a slow provider. Chosen for obviously-correct
        # simplicity at portfolio scale; per-workflow locking is the documented
        # upgrade. Known gap: approval, tool execution, and workflow save are
        # separate transactions, so a crash mid-approve can wedge a workflow — the
        # retry path recovers the common case (see ``approve``); a single
        # unit-of-work is the fuller fix.
        self._lock = threading.RLock()

    # --- Public API ---

    def submit(self, *, text: str, user: str, source: str) -> WorkflowView:
        with self._lock:
            wid = self._id_factory()
            request = OperationRequest(id=wid, text=text, user=user, source=source)
            wf = _Workflow(id=wid, request=request, status=WorkflowStatus.CREATED)
            self._workflows.create(wf)
            self._audit.append(
                wid, AuditEventType.REQUEST_CREATED, actor=user, payload={"source": source}
            )

            plan = self._planner.plan(request)
            wf.summary = plan.summary
            self._to(wf, WorkflowStatus.PLANNED)
            self._audit.append(
                wid,
                AuditEventType.PLAN_GENERATED,
                actor="planner",
                payload={"summary": plan.summary, "steps": len(plan.steps)},
            )

            if plan.requires_clarification:
                wf.requires_clarification = True
                wf.clarification_question = plan.clarification_question
                self._workflows.save(wf)
                return self._view(wf)

            self._to(wf, WorkflowStatus.VALIDATING)
            try:
                validated = self._policy.validate(plan, allowed_tools=self._allowed_tools)
            except OpsAssistantError:
                self._to(wf, WorkflowStatus.FAILED)
                self._audit.append(wid, AuditEventType.WORKFLOW_FAILED, actor="policy")
                self._workflows.save(wf)
                raise

            wf.plan_fingerprint = plan_fingerprint(plan)
            self._audit.append(
                wid,
                AuditEventType.PLAN_VALIDATED,
                actor="policy",
                payload={"steps": len(validated.steps)},
            )
            for vs in validated.steps:
                if vs.risk_mismatch:
                    self._audit.append(
                        wid,
                        AuditEventType.RISK_MISMATCH_DETECTED,
                        actor="policy",
                        step_id=vs.id,
                        payload={
                            "tool": vs.tool,
                            "claimed": vs.claimed_risk.value if vs.claimed_risk else None,
                            "resolved": vs.resolved_risk.value,
                        },
                    )
            wf.steps = [_Step(validated=vs) for vs in validated.steps]

            self._run(wf)
            self._workflows.save(wf)
            return self._view(wf)

    def approve(
        self, workflow_id: str, approval_id: str, *, actor: str, reason: str | None = None
    ) -> WorkflowView:
        with self._lock:
            wf = self._guard_decision(workflow_id, approval_id)
            approval = self._approvals.approve(
                approval_id, actor=actor, plan_fingerprint=wf.plan_fingerprint, reason=reason
            )
            self._audit.append(
                wf.id,
                AuditEventType.APPROVAL_APPROVED,
                actor=actor,
                step_id=approval.step_id,
                payload={"approval_id": approval_id, "tool": approval.tool},
            )
            self._to(wf, WorkflowStatus.APPROVED)
            self._ensure_executing(wf)
            self._execute_step(wf, self._step(wf, approval.step_id))
            self._run(wf)
            self._workflows.save(wf)
            return self._view(wf)

    def reject(
        self, workflow_id: str, approval_id: str, *, actor: str, reason: str | None = None
    ) -> WorkflowView:
        with self._lock:
            wf = self._guard_decision(workflow_id, approval_id)
            approval = self._approvals.reject(
                approval_id, actor=actor, plan_fingerprint=wf.plan_fingerprint, reason=reason
            )
            self._audit.append(
                wf.id,
                AuditEventType.APPROVAL_REJECTED,
                actor=actor,
                step_id=approval.step_id,
                payload={"approval_id": approval_id, "reason": reason},
            )
            self._set_step(wf, self._step(wf, approval.step_id), StepStatus.REJECTED)
            # Rejecting an action declines the proposed plan: void every sibling
            # approval and skip its step so no live approval outlives the workflow.
            for sibling in self._approvals.pending_for_workflow(wf.id):
                self._approvals.cancel(sibling.id)
                self._set_step(wf, self._step(wf, sibling.step_id), StepStatus.SKIPPED)
                self._audit.append(
                    wf.id,
                    AuditEventType.STEP_SKIPPED,
                    actor="system",
                    step_id=sibling.step_id,
                    payload={"reason": "another step in the plan was rejected"},
                )
            self._propagate_skips(wf)
            self._to(wf, WorkflowStatus.REJECTED)
            self._workflows.save(wf)
            return self._view(wf)

    def approve_pending(
        self, approval_id: str, *, actor: str, reason: str | None = None
    ) -> WorkflowView:
        """Approve by approval id alone, resolving its workflow (used by callbacks)."""
        with self._lock:
            approval = self._approvals.get(approval_id)  # ApprovalNotFoundError if missing
            return self.approve(approval.workflow_id, approval_id, actor=actor, reason=reason)

    def reject_pending(
        self, approval_id: str, *, actor: str, reason: str | None = None
    ) -> WorkflowView:
        with self._lock:
            approval = self._approvals.get(approval_id)
            return self.reject(approval.workflow_id, approval_id, actor=actor, reason=reason)

    def _guard_decision(self, workflow_id: str, approval_id: str) -> _Workflow:
        """Fetch the workflow and verify a decision is legal *before* any mutation.

        Confirms the approval belongs to this workflow and the workflow is still
        awaiting approval, so a decision can never consume an approval or write an
        audit record against a terminal or mismatched workflow.
        """
        wf = self._require(workflow_id)
        approval = self._approvals.get(approval_id)  # ApprovalNotFoundError if missing
        if approval.workflow_id != workflow_id:
            raise NotFoundError(f"approval {approval_id} does not belong to workflow {workflow_id}")
        if wf.status is not WorkflowStatus.AWAITING_APPROVAL:
            raise StateTransitionError(
                f"workflow {workflow_id} is {wf.status.value}; it is not awaiting approval"
            )
        return wf

    def get(self, workflow_id: str) -> WorkflowView:
        return self._view(self._require(workflow_id))

    def audit_for(self, workflow_id: str) -> tuple[AuditEvent, ...]:
        return self._audit.for_workflow(workflow_id)

    def all_audit(self) -> tuple[AuditEvent, ...]:
        return self._audit.events()

    # --- Execution engine ---

    def _run(self, wf: _Workflow) -> None:
        """Make all possible progress, then settle the workflow status."""
        while True:
            self._propagate_skips(wf)
            ready = self._ready_auto_steps(wf)
            if not ready:
                break
            self._ensure_executing(wf)
            for step in ready:
                self._execute_step(wf, step)

        for step in self._ready_approval_steps(wf):
            self._request_approval(wf, step)

        self._settle(wf)

    def _propagate_skips(self, wf: _Workflow) -> None:
        changed = True
        while changed:
            changed = False
            for step in wf.steps:
                if step.status is not StepStatus.PENDING:
                    continue
                deps = [self._step(wf, d) for d in step.validated.depends_on]
                blocked = any(
                    d.status in (StepStatus.FAILED, StepStatus.REJECTED, StepStatus.SKIPPED)
                    or (d.status is StepStatus.SUCCEEDED and _is_empty(d.output))
                    for d in deps
                )
                if blocked:
                    self._set_step(wf, step, StepStatus.SKIPPED)
                    self._audit.append(
                        wf.id,
                        AuditEventType.STEP_SKIPPED,
                        actor="executor",
                        step_id=step.validated.id,
                        payload={"reason": "upstream dependency not satisfied"},
                    )
                    changed = True

    def _succeeded_outputs(self, wf: _Workflow) -> dict[str, object]:
        return {
            s.validated.id: s.output
            for s in wf.steps
            if s.status is StepStatus.SUCCEEDED and s.output is not None
        }

    def _deps_succeeded(self, wf: _Workflow, step: _Step) -> bool:
        return all(
            self._step(wf, d).status is StepStatus.SUCCEEDED for d in step.validated.depends_on
        )

    def _ready_auto_steps(self, wf: _Workflow) -> list[_Step]:
        return [
            s
            for s in wf.steps
            if s.status is StepStatus.PENDING
            and s.validated.decision is ApprovalDecision.AUTO
            and self._deps_succeeded(wf, s)
        ]

    def _ready_approval_steps(self, wf: _Workflow) -> list[_Step]:
        return [
            s
            for s in wf.steps
            if s.status is StepStatus.PENDING
            and s.validated.decision is ApprovalDecision.REQUIRES_APPROVAL
            and s.approval_id is None
            and self._deps_succeeded(wf, s)
        ]

    def _execute_step(self, wf: _Workflow, step: _Step) -> None:
        self._set_step(wf, step, StepStatus.RUNNING)
        arguments = resolve_references(step.validated.arguments, self._succeeded_outputs(wf))
        self._audit.append(
            wf.id,
            AuditEventType.STEP_STARTED,
            actor="executor",
            step_id=step.validated.id,
            payload={
                "tool": step.validated.tool,
                "arguments": _redact_for_audit(step.validated.arguments, arguments),
            },
        )
        try:
            result = self._gateway.execute(
                wf.id,
                step.validated.id,
                step.validated.tool,
                arguments,
                idempotency_key=f"{wf.id}:{step.validated.id}",
            )
        except OpsAssistantError as exc:
            step.error = exc.message
            self._set_step(wf, step, StepStatus.FAILED)
            self._audit.append(
                wf.id,
                AuditEventType.STEP_FAILED,
                actor="executor",
                step_id=step.validated.id,
                payload={"tool": step.validated.tool},
            )
            return
        step.output = result.output
        self._set_step(wf, step, StepStatus.SUCCEEDED)
        self._audit.append(
            wf.id,
            AuditEventType.STEP_SUCCEEDED,
            actor="executor",
            step_id=step.validated.id,
            payload={"tool": step.validated.tool, "result": _result_digest(result.output)},
        )

    def _request_approval(self, wf: _Workflow, step: _Step) -> None:
        # Resolve ``{{step.field}}`` references now so the approval — and every
        # surface that renders it — shows the *real* recipient/body, not a
        # placeholder. Policy guarantees every reference is a declared dependency,
        # and a step is only ready for approval once its deps have succeeded, so
        # this resolves against exactly the frozen outputs the executor will use:
        # what the human approves is what runs. (The plan-bound fingerprint already
        # binds the action, so it needn't fold in the resolved values.)
        arguments = resolve_references(step.validated.arguments, self._succeeded_outputs(wf))
        approval = self._approvals.request(
            workflow_id=wf.id,
            step_id=step.validated.id,
            plan_fingerprint=wf.plan_fingerprint,
            tool=step.validated.tool,
            arguments=arguments,
            risk=step.validated.resolved_risk.value,
            ttl=self._ttl,
        )
        step.approval_id = approval.id
        self._set_step(wf, step, StepStatus.AWAITING_APPROVAL)
        self._audit.append(
            wf.id,
            AuditEventType.APPROVAL_REQUESTED,
            actor="system",
            step_id=step.validated.id,
            payload={
                "tool": step.validated.tool,
                "approval_id": approval.id,
                "risk": step.validated.resolved_risk.value,
                "arguments": _redact_for_audit(step.validated.arguments, arguments),
            },
        )

    def _settle(self, wf: _Workflow) -> None:
        if any(s.status is StepStatus.AWAITING_APPROVAL for s in wf.steps):
            self._to(wf, WorkflowStatus.AWAITING_APPROVAL)
            return
        if any(s.status in (StepStatus.PENDING, StepStatus.RUNNING) for s in wf.steps):
            return  # pragma: no cover - defensive: nothing runnable should reach settle
        if any(s.status is StepStatus.FAILED for s in wf.steps):
            self._ensure_executing(wf)
            self._to(wf, WorkflowStatus.FAILED)
            self._audit.append(wf.id, AuditEventType.WORKFLOW_FAILED, actor="system")
            return
        self._ensure_executing(wf)
        self._to(wf, WorkflowStatus.COMPLETED)
        self._audit.append(wf.id, AuditEventType.WORKFLOW_COMPLETED, actor="system")

    # --- Transitions & lookups ---

    def _ensure_executing(self, wf: _Workflow) -> None:
        if wf.status is not WorkflowStatus.EXECUTING:
            self._to(wf, WorkflowStatus.EXECUTING)

    def _to(self, wf: _Workflow, target: WorkflowStatus) -> None:
        assert_workflow_transition(wf.status, target)
        wf.status = target

    def _set_step(self, wf: _Workflow, step: _Step, target: StepStatus) -> None:
        assert_step_transition(step.status, target)
        step.status = target

    def _require(self, workflow_id: str) -> _Workflow:
        wf = self._workflows.get(workflow_id)
        if wf is None:
            raise NotFoundError(f"no such workflow: {workflow_id}")
        return wf

    def _step(self, wf: _Workflow, step_id: str) -> _Step:
        for step in wf.steps:
            if step.validated.id == step_id:
                return step
        raise NotFoundError(f"no such step: {step_id}")  # pragma: no cover - defensive

    # --- View building ---

    def _view(self, wf: _Workflow) -> WorkflowView:
        pending: Sequence[Approval] = self._approvals.pending_for_workflow(wf.id)
        return WorkflowView(
            id=wf.id,
            status=wf.status,
            summary=wf.summary,
            requires_clarification=wf.requires_clarification,
            clarification_question=wf.clarification_question,
            steps=[
                StepView(
                    id=s.validated.id,
                    tool=s.validated.tool,
                    arguments=s.validated.arguments,
                    depends_on=s.validated.depends_on,
                    resolved_risk=s.validated.resolved_risk,
                    decision=s.validated.decision,
                    status=s.status,
                    risk_mismatch=s.validated.risk_mismatch,
                    approval_id=s.approval_id,
                    output=s.output,
                    error=s.error,
                )
                for s in wf.steps
            ],
            pending_approvals=[
                ApprovalView(
                    id=a.id, step_id=a.step_id, tool=a.tool, arguments=a.arguments, risk=a.risk
                )
                for a in pending
            ],
        )
