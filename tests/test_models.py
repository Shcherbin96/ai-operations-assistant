"""Contracts: risk tiers, planner-facing plan/step models, stable fingerprints."""

import pytest
from pydantic import ValidationError

from ops_assistant.models import (
    OperationRequest,
    Plan,
    PlanStep,
    RiskTier,
    plan_fingerprint,
)


def test_risk_tier_string_values() -> None:
    assert RiskTier.READ_ONLY == "read_only"
    assert RiskTier.DRAFT == "draft"
    assert RiskTier.WRITE == "write"
    assert RiskTier.EXTERNAL_SIDE_EFFECT == "external_side_effect"
    assert RiskTier.DESTRUCTIVE == "destructive"


def test_risk_tier_rank_is_monotonic() -> None:
    ordered = [
        RiskTier.READ_ONLY,
        RiskTier.DRAFT,
        RiskTier.WRITE,
        RiskTier.EXTERNAL_SIDE_EFFECT,
        RiskTier.DESTRUCTIVE,
    ]
    ranks = [t.rank for t in ordered]
    assert ranks == sorted(ranks)
    assert len(set(ranks)) == len(ranks)
    assert RiskTier.READ_ONLY.rank < RiskTier.WRITE.rank
    assert RiskTier.EXTERNAL_SIDE_EFFECT.rank > RiskTier.WRITE.rank


def test_plan_step_defaults() -> None:
    step = PlanStep(id="s1", tool="email.search", arguments={"query": "x"})
    assert step.depends_on == []
    assert step.claimed_risk is None
    assert step.reason == ""


def test_plan_step_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        PlanStep(id="s1", tool="email.search", arguments={}, bogus=1)  # type: ignore[call-arg]


def test_plan_requires_clarification_defaults_false() -> None:
    plan = Plan(summary="do a thing", steps=[])
    assert plan.requires_clarification is False
    assert plan.clarification_question is None


def test_plan_fingerprint_is_stable_for_equal_plans() -> None:
    a = Plan(summary="s", steps=[PlanStep(id="1", tool="email.search", arguments={"q": 1})])
    b = Plan(summary="s", steps=[PlanStep(id="1", tool="email.search", arguments={"q": 1})])
    assert plan_fingerprint(a) == plan_fingerprint(b)


def test_plan_fingerprint_changes_when_a_step_changes() -> None:
    base = Plan(summary="s", steps=[PlanStep(id="1", tool="email.search", arguments={"q": 1})])
    changed = Plan(summary="s", steps=[PlanStep(id="1", tool="email.send", arguments={"q": 1})])
    assert plan_fingerprint(base) != plan_fingerprint(changed)


def test_plan_fingerprint_ignores_advisory_risk_label() -> None:
    # The model's self-reported risk is advisory; it must not change the identity
    # of the plan the user approves — otherwise a relabel would silently
    # invalidate an approval without changing what actually runs.
    honest = Plan(
        summary="s",
        steps=[
            PlanStep(
                id="1", tool="email.send", arguments={}, claimed_risk=RiskTier.EXTERNAL_SIDE_EFFECT
            )
        ],
    )
    lying = Plan(
        summary="s",
        steps=[PlanStep(id="1", tool="email.send", arguments={}, claimed_risk=RiskTier.READ_ONLY)],
    )
    assert plan_fingerprint(honest) == plan_fingerprint(lying)


def test_operation_request_is_immutable() -> None:
    req = OperationRequest(id="r1", text="hi", user="u1", source="telegram")
    with pytest.raises(ValidationError):
        req.text = "changed"  # type: ignore[misc]
