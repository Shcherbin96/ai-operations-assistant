"""End-to-end orchestration: submit -> validate -> auto-run -> approve -> execute.

This is where the whole thesis becomes observable: read-only work runs on its own,
external side-effects wait for a human, the model's risk label cannot unlock an
action, and everything is auditable.
"""

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from ops_assistant.audit import AuditEventType
from ops_assistant.models import (
    OperationRequest,
    Plan,
    PlanStep,
    RiskTier,
    StepStatus,
    WorkflowStatus,
)
from ops_assistant.policy import PolicyConfig
from ops_assistant.service import OpsService
from ops_assistant.tools.registry import ToolRegistry, ToolSpec
from ops_assistant.tools.sandbox import build_sandbox_registry


def _counter_ids() -> Callable[[], str]:
    n = iter(range(1, 100000))

    def factory() -> str:
        return f"id-{next(n)}"

    return factory


def _service(**kw: object) -> OpsService:
    kw.setdefault("clock", lambda: datetime(2026, 7, 19, 12, 0, tzinfo=UTC))
    kw.setdefault("id_factory", _counter_ids())
    return OpsService(**kw)  # type: ignore[arg-type]


# --- read-only work runs automatically ---


def test_find_free_time_completes_automatically() -> None:
    svc = _service()
    view = svc.submit(text="find free time tomorrow", user="roman", source="test")
    assert view.status is WorkflowStatus.COMPLETED
    assert view.steps[0].status is StepStatus.SUCCEEDED
    assert view.steps[0].output is not None


def test_draft_flow_runs_without_sending_anything() -> None:
    svc = _service()
    view = svc.submit(text="draft replies to recent emails", user="roman", source="test")
    assert view.status is WorkflowStatus.COMPLETED
    tools_done = {s.tool for s in view.steps if s.status is StepStatus.SUCCEEDED}
    assert "email.search" in tools_done
    assert "email.create_draft" in tools_done
    events = [e.event_type for e in svc.audit_for(view.id)]
    assert AuditEventType.TOOL_SUCCEEDED in events


# --- external side-effects wait for a human ---


def test_send_pauses_for_approval_and_does_not_execute() -> None:
    svc = _service()
    view = svc.submit(text="send an email to anna@example.com", user="roman", source="test")
    assert view.status is WorkflowStatus.AWAITING_APPROVAL
    send_step = next(s for s in view.steps if s.tool == "email.send")
    assert send_step.status is StepStatus.AWAITING_APPROVAL
    assert len(view.pending_approvals) == 1
    # nothing was actually sent
    sent = [e for e in svc.audit_for(view.id) if e.event_type is AuditEventType.TOOL_SUCCEEDED]
    assert sent == []


def test_approving_the_send_executes_it_and_completes() -> None:
    svc = _service()
    view = svc.submit(text="send an email to anna@example.com", user="roman", source="test")
    approval_id = view.pending_approvals[0].id
    done = svc.approve(view.id, approval_id, actor="roman")
    assert done.status is WorkflowStatus.COMPLETED
    send_step = next(s for s in done.steps if s.tool == "email.send")
    assert send_step.status is StepStatus.SUCCEEDED
    assert send_step.output is not None
    events = [e.event_type for e in svc.audit_for(view.id)]
    assert AuditEventType.APPROVAL_APPROVED in events
    assert AuditEventType.TOOL_SUCCEEDED in events


def test_rejecting_the_send_completes_as_rejected_without_sending() -> None:
    svc = _service()
    view = svc.submit(text="send an email to anna@example.com", user="roman", source="test")
    approval_id = view.pending_approvals[0].id
    done = svc.reject(view.id, approval_id, actor="roman", reason="not now")
    assert done.status is WorkflowStatus.REJECTED
    send_step = next(s for s in done.steps if s.tool == "email.send")
    assert send_step.status is StepStatus.REJECTED
    sent = [e for e in svc.audit_for(view.id) if e.event_type is AuditEventType.TOOL_SUCCEEDED]
    assert sent == []


def test_double_approval_is_rejected() -> None:
    # After the single gated step runs, the workflow is completed, so a second
    # approve is refused by the workflow-state guard before touching the approval.
    from ops_assistant.errors import StateTransitionError

    svc = _service()
    view = svc.submit(text="send an email to anna@example.com", user="roman", source="test")
    approval_id = view.pending_approvals[0].id
    svc.approve(view.id, approval_id, actor="roman")
    with pytest.raises(StateTransitionError):
        svc.approve(view.id, approval_id, actor="roman")


# --- the headline guarantee: a hostile plan cannot self-authorize a send ---


def test_approve_pending_resolves_the_workflow_from_the_approval() -> None:
    svc = _service()
    view = svc.submit(text="send an email to anna@example.com", user="roman", source="test")
    done = svc.approve_pending(view.pending_approvals[0].id, actor="roman")
    assert done.status is WorkflowStatus.COMPLETED


def test_reject_pending_resolves_the_workflow_from_the_approval() -> None:
    svc = _service()
    view = svc.submit(text="send an email to anna@example.com", user="roman", source="test")
    done = svc.reject_pending(view.pending_approvals[0].id, actor="roman", reason="no")
    assert done.status is WorkflowStatus.REJECTED


def test_approve_pending_unknown_approval_raises() -> None:
    from ops_assistant.errors import ApprovalNotFoundError

    with pytest.raises(ApprovalNotFoundError):
        _service().approve_pending("nope", actor="roman")


class _RefPlanner:
    """Step 2 references step 1's output via {{s1.from}}."""

    def plan(self, request: OperationRequest) -> Plan:
        return Plan(
            summary="Reply to the first email",
            steps=[
                PlanStep(id="s1", tool="email.search", arguments={"query": "all"}),
                PlanStep(
                    id="s2",
                    tool="email.create_draft",
                    arguments={"to": "{{s1.from}}", "body": "Re: {{s1.subject}}"},
                    depends_on=["s1"],
                ),
            ],
        )


def test_step_arguments_are_resolved_from_an_earlier_step_output() -> None:
    svc = _service(planner=_RefPlanner())
    view = svc.submit(text="reply to my first email", user="roman", source="test")
    assert view.status is WorkflowStatus.COMPLETED
    draft = next(s for s in view.steps if s.tool == "email.create_draft")
    # The sandbox search returns anna@example.com as the first sender; the draft's
    # recipient must be that real value, not the literal "{{s1.from}}" placeholder.
    assert draft.output["to"] == "anna@example.com"


class _GatedRefPlanner:
    """A GATED send whose recipient/body are resolved from a prior read step."""

    def plan(self, request: OperationRequest) -> Plan:
        return Plan(
            summary="Reply to the first email",
            steps=[
                PlanStep(id="s1", tool="email.search", arguments={"query": "all"}),
                PlanStep(
                    id="s2",
                    tool="email.send",
                    arguments={"to": "{{s1.from}}", "body": "Thanks re: {{s1.subject}}"},
                    depends_on=["s1"],
                ),
            ],
        )


def test_approval_preview_shows_the_resolved_recipient_not_a_placeholder() -> None:
    # The whole thesis of HITL: the human must see what they approve. For a
    # data-flow send, the preview must show the real recipient, never "{{s1.from}}".
    svc = _service(planner=_GatedRefPlanner())
    view = svc.submit(text="reply to my first email", user="roman", source="test")
    assert view.status is WorkflowStatus.AWAITING_APPROVAL
    approval = view.pending_approvals[0]
    assert approval.arguments["to"] == "anna@example.com"  # resolved, not the template

    # And what executes equals what was approved.
    outcome = svc.approve_pending(approval.id, actor="roman")
    send = next(s for s in outcome.steps if s.tool == "email.send")
    assert send.output["to"] == "anna@example.com"


def test_audit_records_the_resolved_recipient_and_redacts_the_body() -> None:
    svc = _service(planner=_GatedRefPlanner())
    view = svc.submit(text="reply to my first email", user="roman", source="test")
    svc.approve_pending(view.pending_approvals[0].id, actor="roman")

    started = [
        e
        for e in svc.audit_for(view.id)
        if e.event_type is AuditEventType.STEP_STARTED and e.payload.get("tool") == "email.send"
    ]
    assert started, "the executed send should be audited"
    arguments = started[0].payload["arguments"]
    assert arguments["to"] == "anna@example.com"  # forensic recipient is recorded...
    assert "redacted" in str(arguments["body"]).lower()  # ...but the body is not stored verbatim


class _InjectionRefPlanner:
    """Reads the injected (attacker-controlled) message, then a GATED send to its
    'from' — the exact untrusted-content-into-a-side-effect vector data-flow opened."""

    def plan(self, request: OperationRequest) -> Plan:
        from ops_assistant.tools.sandbox import INJECTED_MESSAGE_ID

        return Plan(
            summary="Reply to that message",
            steps=[
                PlanStep(id="s1", tool="email.get", arguments={"id": INJECTED_MESSAGE_ID}),
                PlanStep(
                    id="s2",
                    tool="email.send",
                    arguments={"to": "{{s1.from}}", "body": "sure"},
                    depends_on=["s1"],
                ),
            ],
        )


def test_untrusted_sender_flows_into_a_gated_send_visibly_and_never_auto_runs() -> None:
    svc = _service(planner=_InjectionRefPlanner())
    view = svc.submit(text="reply to the latest email", user="roman", source="test")
    # The send is an external side-effect: gated, never auto-executed...
    assert view.status is WorkflowStatus.AWAITING_APPROVAL
    approval = view.pending_approvals[0]
    # ...and the human sees the ACTUAL attacker recipient before deciding.
    assert approval.arguments["to"] == "attacker@example.net"
    outcome = svc.approve_pending(approval.id, actor="roman")
    send = next(s for s in outcome.steps if s.tool == "email.send")
    assert send.output["to"] == "attacker@example.net"  # executed == approved


class _UndeclaredRefPlanner:
    """References s1 without declaring the dependency — a redirect the preview
    would never resolve. Policy must refuse it."""

    def plan(self, request: OperationRequest) -> Plan:
        return Plan(
            summary="redirect",
            steps=[
                PlanStep(id="s1", tool="email.search", arguments={"query": "all"}),
                PlanStep(id="s2", tool="email.send", arguments={"to": "{{s1.from}}"}),
            ],
        )


def test_reference_without_a_declared_dependency_is_refused() -> None:
    from ops_assistant.errors import PlanValidationError

    svc = _service(planner=_UndeclaredRefPlanner())
    with pytest.raises(PlanValidationError):
        svc.submit(text="reply", user="roman", source="test")


def test_approve_recovers_from_a_mid_approve_save_failure_without_refiring() -> None:
    import copy

    from ops_assistant.errors import ConflictError
    from ops_assistant.state import WorkflowRecord

    calls = {"n": 0}

    def _send(args: object) -> object:
        calls["n"] += 1
        return {"message_id": "m1", "status": "sent"}

    registry = build_sandbox_registry()
    registry.register(ToolSpec("test.send", RiskTier.EXTERNAL_SIDE_EFFECT, "counting send", _send))

    class _P:
        def plan(self, request: OperationRequest) -> Plan:
            return Plan(summary="x", steps=[PlanStep(id="s1", tool="test.send", arguments={})])

    class _FlakyStore:
        """Postgres-like: get/save copy the record (so a failed save doesn't persist),
        and the next save after arming raises once — simulating an optimistic-lock
        conflict or a crash between the approval CAS and the workflow save."""

        def __init__(self) -> None:
            self._data: dict[str, WorkflowRecord] = {}
            self.fail_next_save = False

        def create(self, workflow: WorkflowRecord) -> None:
            self._data[workflow.id] = copy.deepcopy(workflow)

        def get(self, workflow_id: str) -> WorkflowRecord | None:
            wf = self._data.get(workflow_id)
            return copy.deepcopy(wf) if wf is not None else None

        def save(self, workflow: WorkflowRecord) -> None:
            if self.fail_next_save:
                self.fail_next_save = False
                raise ConflictError("simulated mid-approve save failure")
            self._data[workflow.id] = copy.deepcopy(workflow)

    store = _FlakyStore()
    svc = _service(planner=_P(), registry=registry, store=store)
    view = svc.submit(text="go", user="u", source="test")
    assert view.status is WorkflowStatus.AWAITING_APPROVAL
    approval_id = view.pending_approvals[0].id

    # Attempt 1: the approval CAS commits, then the workflow save fails -> wedge
    # (approval APPROVED, workflow still AWAITING_APPROVAL). The side-effect fired.
    store.fail_next_save = True
    with pytest.raises(ConflictError):
        svc.approve(view.id, approval_id, actor="u")
    assert calls["n"] == 1

    # Attempt 2: recovers instead of raising ApprovalAlreadyDecided, and the
    # idempotent gateway replays the completed step rather than firing it again.
    recovered = svc.approve(view.id, approval_id, actor="u")
    assert recovered.status is WorkflowStatus.COMPLETED
    assert calls["n"] == 1  # exactly once, total


def test_deployment_tool_allowlist_refuses_off_list_tools() -> None:
    from ops_assistant.errors import ToolNotAllowedError

    # Restrict the deployment to read-only search; a plan that tries to send is refused.
    svc = _service(allowed_tools=frozenset({"email.search"}))
    with pytest.raises(ToolNotAllowedError):
        svc.submit(text="send an email to anna@example.com", user="roman", source="test")


def test_deployment_tool_allowlist_permits_listed_tools() -> None:
    svc = _service(allowed_tools=frozenset({"calendar.find_free_time"}))
    view = svc.submit(text="find free time tomorrow", user="roman", source="test")
    assert view.status is WorkflowStatus.COMPLETED


def test_redact_for_audit_keeps_forensics_and_literals_but_summarizes_referenced_data() -> None:
    from ops_assistant.service import _redact_for_audit

    template = {
        "to": "{{s1.from}}",  # reference into a routing field -> kept (forensic)
        "subject": "Weekly report",  # literal plan text -> kept
        "context": "{{s1.body}}",  # a body routed into a renamed key -> summarized
        "body": "{{s1}}",  # whole-output nested dict under a body key -> summarized
        "count": 5,  # literal scalar -> kept
        "cc": ["a@b.c", "d@e.f"],  # literal structured value -> shape only
    }
    resolved = {
        "to": "anna@example.com",
        "subject": "Weekly report",
        "context": "Hi, quick question about my order.",  # short body, not a body-key
        "body": {"from": "a@b.c", "body": "secret full text"},
        "count": 5,
        "cc": ["a@b.c", "d@e.f"],
    }
    out = _redact_for_audit(template, resolved)
    assert out["to"] == "anna@example.com"  # recipient recorded (the forensic point)
    assert out["subject"] == "Weekly report"  # literal kept
    assert out["count"] == 5
    assert out["cc"] == {"redacted_items": 2}  # structured value never dumped verbatim
    assert out["context"].startswith("<redacted")  # reference-derived body-ish -> summarized
    assert out["body"] == {"redacted_keys": ["body", "from"]}  # nested body never stored
    # No sensitive text survives anywhere in the payload:
    assert "secret full text" not in str(out)
    assert "Hi, quick question about my order." not in str(out)


def test_redact_for_audit_never_dumps_a_body_by_type() -> None:
    from ops_assistant.service import _redact_for_audit

    template = {"body": ["a", "b"], "html": "{{s1.n}}"}
    resolved = {"body": ["line one", "line two"], "html": 7}
    out = _redact_for_audit(template, resolved)
    assert out["body"] == {"redacted_items": 2}  # list body -> shape only
    assert out["html"] == 7  # non-string under a body key -> harmless scalar
    assert "line one" not in str(out)


def test_result_digest_summarizes_each_shape() -> None:
    from ops_assistant.service import _result_digest

    assert _result_digest([1, 2, 3]) == {"count": 3}  # lists -> just a count
    got = _result_digest({"message_id": "m1", "from": "a@b.c", "body": "secret"})
    assert got["message_id"] == "m1" and got["from"] == "a@b.c"  # forensics kept
    assert got["body"].startswith("<redacted") and "secret" not in str(got)  # body redacted
    assert str(_result_digest("x" * 300)).endswith("chars)")  # long scalar capped
    assert _result_digest("ok") == "ok"  # short scalar passes through


class _MaliciousPlanner:
    """Simulates a planner subverted by injected email content: it slips in a send
    step and lies that it is read_only, hoping it auto-executes."""

    def plan(self, request: OperationRequest) -> Plan:
        return Plan(
            summary="Summarize inbox",
            steps=[
                PlanStep(id="s1", tool="email.search", arguments={"query": "all"}),
                PlanStep(
                    id="s2",
                    tool="email.send",
                    arguments={"to": "attacker@example.net", "body": "customer data"},
                    claimed_risk=RiskTier.READ_ONLY,
                ),
            ],
        )


def test_injected_send_is_never_auto_executed_and_is_flagged() -> None:
    svc = _service(planner=_MaliciousPlanner())
    view = svc.submit(text="summarize my inbox", user="roman", source="test")

    # The server re-derived the real risk; the send is gated, not run.
    assert view.status is WorkflowStatus.AWAITING_APPROVAL
    send_step = next(s for s in view.steps if s.tool == "email.send")
    assert send_step.status is StepStatus.AWAITING_APPROVAL
    assert send_step.risk_mismatch is True

    events = [e.event_type for e in svc.audit_for(view.id)]
    assert AuditEventType.RISK_MISMATCH_DETECTED in events
    sent = [e for e in svc.audit_for(view.id) if e.event_type is AuditEventType.TOOL_SUCCEEDED]
    assert all(e.payload.get("tool") != "email.send" for e in sent)


# --- dependency handling: empty upstream result skips downstream steps ---


def test_empty_search_skips_the_dependent_draft_step() -> None:
    reg: ToolRegistry = build_sandbox_registry()
    # Replace search with one that finds nothing.
    empty_reg = ToolRegistry()
    for name in reg.names():
        spec = reg.require(name)
        if name == "email.search":
            empty_reg.register(ToolSpec(name, spec.risk, spec.description, lambda a: []))
        else:
            empty_reg.register(spec)

    svc = _service(registry=empty_reg)
    view = svc.submit(text="draft replies to recent emails", user="roman", source="test")
    assert view.status is WorkflowStatus.COMPLETED
    draft = next(s for s in view.steps if s.tool == "email.create_draft")
    assert draft.status is StepStatus.SKIPPED


# --- clarification short-circuits execution ---


class _FreeTimePlanner:
    """Emits a single read-only step whose tool the test controls."""

    def __init__(self, tool: str) -> None:
        self._tool = tool

    def plan(self, request: OperationRequest) -> Plan:
        return Plan(summary="one step", steps=[PlanStep(id="s1", tool=self._tool, arguments={})])


def test_failing_tool_fails_the_workflow_and_is_audited() -> None:
    # Golden scenario: an external service errors mid-run -> the workflow stops,
    # the step is marked failed, and it is recorded.
    def boom(args: object) -> object:
        raise RuntimeError("calendar API down")

    reg = build_sandbox_registry()
    failing = ToolRegistry()
    for name in reg.names():
        spec = reg.require(name)
        failing.register(
            ToolSpec(name, spec.risk, spec.description, boom, spec.required_args)
            if name == "calendar.find_free_time"
            else spec
        )

    svc = _service(planner=_FreeTimePlanner("calendar.find_free_time"), registry=failing)
    view = svc.submit(text="whatever", user="roman", source="test")
    assert view.status is WorkflowStatus.FAILED
    assert view.steps[0].status is StepStatus.FAILED
    assert view.steps[0].error
    assert AuditEventType.WORKFLOW_FAILED in [e.event_type for e in svc.audit_for(view.id)]


class _TwoSendPlanner:
    """Two independent external sends — the multi-approval case."""

    def plan(self, request: OperationRequest) -> Plan:
        return Plan(
            summary="Send two emails",
            steps=[
                PlanStep(id="s1", tool="email.send", arguments={"to": "a@x.com"}),
                PlanStep(id="s2", tool="email.send", arguments={"to": "b@x.com"}),
            ],
        )


def test_two_gated_steps_can_both_be_approved() -> None:
    svc = _service(planner=_TwoSendPlanner())
    view = svc.submit(text="send two", user="roman", source="test")
    assert view.status is WorkflowStatus.AWAITING_APPROVAL
    assert len(view.pending_approvals) == 2

    first = view.pending_approvals[0].id
    view = svc.approve(view.id, first, actor="roman")
    assert view.status is WorkflowStatus.AWAITING_APPROVAL  # one still pending
    second = view.pending_approvals[0].id
    view = svc.approve(view.id, second, actor="roman")
    assert view.status is WorkflowStatus.COMPLETED
    assert all(s.status is StepStatus.SUCCEEDED for s in view.steps)


def test_rejecting_one_gated_step_rejects_workflow_and_cancels_the_sibling() -> None:
    svc = _service(planner=_TwoSendPlanner())
    view = svc.submit(text="send two", user="roman", source="test")
    reject_id = view.pending_approvals[0].id
    view = svc.reject(view.id, reject_id, actor="roman", reason="no")

    assert view.status is WorkflowStatus.REJECTED
    assert view.pending_approvals == []  # sibling approval was cancelled
    statuses = {s.status for s in view.steps}
    assert StepStatus.REJECTED in statuses
    assert StepStatus.SKIPPED in statuses
    # nothing was sent
    sent = [e for e in svc.audit_for(view.id) if e.event_type is AuditEventType.TOOL_SUCCEEDED]
    assert sent == []


def test_deciding_a_sibling_after_rejection_raises_without_corrupting_state() -> None:
    from ops_assistant.errors import StateTransitionError

    svc = _service(planner=_TwoSendPlanner())
    view = svc.submit(text="send two", user="roman", source="test")
    all_ids = [a.id for a in view.pending_approvals]
    svc.reject(view.id, all_ids[0], actor="roman")

    # The sibling approval is gone from the workflow's live set; approving it must
    # fail on the terminal-workflow guard BEFORE any mutation or audit write.
    with pytest.raises(StateTransitionError):
        svc.approve(view.id, all_ids[1], actor="roman")

    events = [e.event_type for e in svc.audit_for(view.id)]
    assert AuditEventType.APPROVAL_APPROVED not in events  # no phantom attestation
    sent = [e for e in svc.audit_for(view.id) if e.event_type is AuditEventType.TOOL_SUCCEEDED]
    assert sent == []


def test_approval_must_belong_to_the_target_workflow() -> None:
    from ops_assistant.errors import NotFoundError

    svc = _service()
    a = svc.submit(text="send an email to anna@example.com", user="roman", source="test")
    b = svc.submit(text="send an email to bob@example.com", user="roman", source="test")
    a_approval = a.pending_approvals[0].id

    with pytest.raises(NotFoundError):
        svc.approve(b.id, a_approval, actor="roman")  # a's approval, b's workflow


def test_concurrent_approval_executes_the_tool_exactly_once() -> None:
    from concurrent.futures import ThreadPoolExecutor

    calls: list[int] = []

    def counting_send(args: object) -> object:
        calls.append(1)
        return {"status": "sent"}

    reg = build_sandbox_registry()
    counted = ToolRegistry()
    for name in reg.names():
        spec = reg.require(name)
        counted.register(
            ToolSpec(name, spec.risk, spec.description, counting_send, spec.required_args)
            if name == "email.send"
            else spec
        )

    svc = _service(registry=counted)
    view = svc.submit(text="send an email to anna@example.com", user="roman", source="test")
    approval_id = view.pending_approvals[0].id

    def worker() -> object:
        try:
            return ("ok", svc.approve(view.id, approval_id, actor="roman").status)
        except Exception as exc:  # noqa: BLE001 - we assert on the type below
            return ("err", type(exc).__name__)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [f.result() for f in [pool.submit(worker), pool.submit(worker)]]

    assert sum(calls) == 1  # the external send fired exactly once
    outcomes = sorted(r[0] for r in results)
    assert outcomes == ["err", "ok"]  # exactly one succeeded, one was refused


class _BadToolPlanner:
    def plan(self, request: OperationRequest) -> Plan:
        return Plan(summary="bad", steps=[PlanStep(id="s1", tool="email.nuke", arguments={})])


def test_plan_with_unknown_tool_fails_the_workflow() -> None:
    from ops_assistant.errors import UnknownToolError

    svc = _service(planner=_BadToolPlanner())
    with pytest.raises(UnknownToolError):
        svc.submit(text="do the thing", user="roman", source="test")


def test_clarification_request_does_not_execute() -> None:
    svc = _service()
    view = svc.submit(text="asdf qwerty", user="roman", source="test")
    assert view.requires_clarification is True
    assert view.clarification_question
    assert view.steps == []


# --- a stricter policy can gate drafts too ---


def test_strict_policy_gates_the_draft_step() -> None:
    strict = PolicyConfig(
        approval_required_tiers=frozenset(
            {RiskTier.DRAFT, RiskTier.WRITE, RiskTier.EXTERNAL_SIDE_EFFECT}
        )
    )
    svc = _service(policy_config=strict)
    view = svc.submit(text="draft replies to recent emails", user="roman", source="test")
    assert view.status is WorkflowStatus.AWAITING_APPROVAL
    assert any(a.tool == "email.create_draft" for a in view.pending_approvals)
