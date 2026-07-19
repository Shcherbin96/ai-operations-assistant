"""The policy engine — the load-bearing guarantee of the whole project.

The model proposes a plan and labels each step's risk. These tests pin down that
the server re-derives the *real* risk from its own registry, and the model's
label can never lower it or unlock an action.
"""

import pytest

from ops_assistant.errors import (
    ArgumentError,
    PlanValidationError,
    PolicyError,
    ToolNotAllowedError,
    UnknownToolError,
)
from ops_assistant.models import Plan, PlanStep, RiskTier
from ops_assistant.policy import ApprovalDecision, PolicyConfig, PolicyEngine
from ops_assistant.tools.sandbox import build_sandbox_registry


def _engine(config: PolicyConfig | None = None) -> PolicyEngine:
    return PolicyEngine(build_sandbox_registry(), config or PolicyConfig())


def test_read_only_step_is_auto_executed() -> None:
    plan = Plan(summary="s", steps=[PlanStep(id="1", tool="email.search", arguments={})])
    validated = _engine().validate(plan)
    step = validated.steps[0]
    assert step.resolved_risk is RiskTier.READ_ONLY
    assert step.decision is ApprovalDecision.AUTO


def test_write_step_requires_approval() -> None:
    plan = Plan(
        summary="s", steps=[PlanStep(id="1", tool="tasks.create", arguments={"title": "t"})]
    )
    step = _engine().validate(plan).steps[0]
    assert step.resolved_risk is RiskTier.WRITE
    assert step.decision is ApprovalDecision.REQUIRES_APPROVAL


def test_external_side_effect_requires_approval() -> None:
    plan = Plan(summary="s", steps=[PlanStep(id="1", tool="email.send", arguments={"to": "a@b.c"})])
    step = _engine().validate(plan).steps[0]
    assert step.resolved_risk is RiskTier.EXTERNAL_SIDE_EFFECT
    assert step.decision is ApprovalDecision.REQUIRES_APPROVAL


def test_model_cannot_lower_risk_to_auto_execute_a_send() -> None:
    # The attack: the model labels a send as read_only, hoping it auto-runs.
    plan = Plan(
        summary="s",
        steps=[
            PlanStep(
                id="1",
                tool="email.send",
                arguments={"to": "a@b.c"},
                claimed_risk=RiskTier.READ_ONLY,
            )
        ],
    )
    step = _engine().validate(plan).steps[0]
    assert step.resolved_risk is RiskTier.EXTERNAL_SIDE_EFFECT  # server's word wins
    assert step.decision is ApprovalDecision.REQUIRES_APPROVAL
    assert step.risk_mismatch is True  # and the lie is flagged


def test_matching_claimed_risk_is_not_a_mismatch() -> None:
    plan = Plan(
        summary="s",
        steps=[
            PlanStep(id="1", tool="email.search", arguments={}, claimed_risk=RiskTier.READ_ONLY)
        ],
    )
    assert _engine().validate(plan).steps[0].risk_mismatch is False


def test_unknown_tool_is_rejected() -> None:
    plan = Plan(summary="s", steps=[PlanStep(id="1", tool="email.nuke", arguments={})])
    with pytest.raises(UnknownToolError):
        _engine().validate(plan)


def test_tool_not_in_allowlist_is_rejected() -> None:
    plan = Plan(summary="s", steps=[PlanStep(id="1", tool="email.send", arguments={"to": "a@b.c"})])
    with pytest.raises(ToolNotAllowedError):
        _engine().validate(plan, allowed_tools={"email.search"})


def test_missing_required_argument_is_rejected() -> None:
    plan = Plan(summary="s", steps=[PlanStep(id="1", tool="email.send", arguments={})])
    with pytest.raises(ArgumentError):
        _engine().validate(plan)


def test_destructive_tool_is_disabled_by_default() -> None:
    plan = Plan(
        summary="s", steps=[PlanStep(id="1", tool="calendar.delete_event", arguments={"id": "e"})]
    )
    with pytest.raises(PolicyError):
        _engine().validate(plan)


def test_dependency_on_unknown_step_is_rejected() -> None:
    plan = Plan(
        summary="s",
        steps=[PlanStep(id="1", tool="email.search", arguments={}, depends_on=["ghost"])],
    )
    with pytest.raises(PlanValidationError):
        _engine().validate(plan)


def test_validated_plan_requires_approval_flag() -> None:
    read_only = Plan(summary="s", steps=[PlanStep(id="1", tool="email.search", arguments={})])
    assert _engine().validate(read_only).requires_approval is False
    gated = Plan(
        summary="s", steps=[PlanStep(id="1", tool="email.send", arguments={"to": "a@b.c"})]
    )
    assert _engine().validate(gated).requires_approval is True


def test_duplicate_step_ids_are_rejected() -> None:
    # depends_on resolves by id; duplicate ids make dependencies ambiguous.
    plan = Plan(
        summary="s",
        steps=[
            PlanStep(id="1", tool="email.search", arguments={}),
            PlanStep(id="1", tool="calendar.list_events", arguments={}),
        ],
    )
    with pytest.raises(PlanValidationError):
        _engine().validate(plan)


def test_cyclic_dependencies_are_rejected() -> None:
    # A hostile/malformed plan whose steps depend on each other would never
    # become runnable and would wedge the workflow forever. Reject at validation.
    plan = Plan(
        summary="s",
        steps=[
            PlanStep(id="1", tool="email.search", arguments={}, depends_on=["2"]),
            PlanStep(id="2", tool="email.search", arguments={}, depends_on=["1"]),
        ],
    )
    with pytest.raises(PlanValidationError):
        _engine().validate(plan)


def test_self_dependency_is_rejected() -> None:
    plan = Plan(
        summary="s",
        steps=[PlanStep(id="1", tool="email.search", arguments={}, depends_on=["1"])],
    )
    with pytest.raises(PlanValidationError):
        _engine().validate(plan)


def test_draft_is_auto_by_default_but_gateable_by_policy() -> None:
    plan = Plan(
        summary="s", steps=[PlanStep(id="1", tool="email.create_draft", arguments={"to": "a@b.c"})]
    )
    assert _engine().validate(plan).steps[0].decision is ApprovalDecision.AUTO

    strict = PolicyConfig(
        approval_required_tiers=frozenset(
            {RiskTier.DRAFT, RiskTier.WRITE, RiskTier.EXTERNAL_SIDE_EFFECT}
        )
    )
    assert _engine(strict).validate(plan).steps[0].decision is ApprovalDecision.REQUIRES_APPROVAL


# --- data-flow references must declare their dependency ---


def test_reference_to_an_undeclared_step_is_rejected() -> None:
    # A step may only reference an output it declared a dependency on — otherwise
    # the approval preview (which resolves against declared deps) could differ from
    # what actually executes.
    plan = Plan(
        summary="sneaky",
        steps=[
            PlanStep(id="s1", tool="email.search", arguments={"query": "all"}),
            PlanStep(id="s2", tool="email.send", arguments={"to": "{{s1.from}}"}),  # no depends_on
        ],
    )
    with pytest.raises(PlanValidationError):
        _engine().validate(plan)


def test_reference_to_a_declared_dependency_is_accepted() -> None:
    plan = Plan(
        summary="reply",
        steps=[
            PlanStep(id="s1", tool="email.search", arguments={"query": "all"}),
            PlanStep(
                id="s2",
                tool="email.send",
                arguments={"to": "{{s1.from}}"},
                depends_on=["s1"],
            ),
        ],
    )
    assert [s.tool for s in _engine().validate(plan).steps] == ["email.search", "email.send"]


def test_literal_template_token_that_names_no_step_is_allowed() -> None:
    # A mail-merge placeholder like {{name}} names no step, so it is not a
    # data-flow reference — the resolver leaves it literal. Policy must not reject
    # a plan just because an argument contains template braces.
    plan = Plan(
        summary="send",
        steps=[
            PlanStep(
                id="s1",
                tool="email.send",
                arguments={"to": "anna@example.com", "body": "Hi {{name}}, your code is ready"},
            )
        ],
    )
    assert _engine().validate(plan).steps[0].tool == "email.send"
