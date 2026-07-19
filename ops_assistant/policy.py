"""The policy engine: turns an untrusted :class:`Plan` into a validated plan.

This is the security core. For every step it:

1. confirms the tool exists (in the registry) and is allowed for the caller,
2. confirms the required arguments are present,
3. re-derives the **real** risk tier from the registry — never from the plan,
4. rejects tiers disabled by policy (e.g. destructive actions in the MVP),
5. decides auto-execute vs. requires-approval from the *server's* tier.

The model's ``claimed_risk`` is only ever compared against the truth to flag
drift; it can never influence the decision.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from ops_assistant.errors import (
    ArgumentError,
    PlanValidationError,
    PolicyError,
    ToolNotAllowedError,
)
from ops_assistant.models import Plan, RiskTier
from ops_assistant.tools.registry import ToolRegistry


class ApprovalDecision(StrEnum):
    AUTO = "auto"
    REQUIRES_APPROVAL = "requires_approval"


class PolicyConfig(BaseModel):
    """Which risk tiers require human approval, and which are disabled outright."""

    model_config = ConfigDict(frozen=True)

    approval_required_tiers: frozenset[RiskTier] = Field(
        default=frozenset({RiskTier.WRITE, RiskTier.EXTERNAL_SIDE_EFFECT})
    )
    disabled_tiers: frozenset[RiskTier] = Field(default=frozenset({RiskTier.DESTRUCTIVE}))

    def decide(self, tier: RiskTier) -> ApprovalDecision:
        if tier in self.approval_required_tiers:
            return ApprovalDecision.REQUIRES_APPROVAL
        return ApprovalDecision.AUTO


class ValidatedStep(BaseModel):
    """A step the server has vouched for, carrying the *resolved* risk tier."""

    model_config = ConfigDict(frozen=True)

    id: str
    tool: str
    arguments: dict[str, object]
    depends_on: list[str]
    reason: str
    resolved_risk: RiskTier
    claimed_risk: RiskTier | None
    risk_mismatch: bool
    decision: ApprovalDecision


class ValidatedPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    summary: str
    steps: list[ValidatedStep]

    @property
    def requires_approval(self) -> bool:
        return any(s.decision is ApprovalDecision.REQUIRES_APPROVAL for s in self.steps)


class PolicyEngine:
    def __init__(self, registry: ToolRegistry, config: PolicyConfig | None = None) -> None:
        self._registry = registry
        self._config = config or PolicyConfig()

    def validate(
        self, plan: Plan, *, allowed_tools: frozenset[str] | set[str] | None = None
    ) -> ValidatedPlan:
        ids = [s.id for s in plan.steps]
        if len(set(ids)) != len(ids):
            raise PlanValidationError("plan contains duplicate step ids")
        known_ids = set(ids)

        for step in plan.steps:
            for dep in step.depends_on:
                if dep not in known_ids:
                    raise PlanValidationError(f"step {step.id} depends on unknown step '{dep}'")
        _reject_dependency_cycles(plan)

        validated: list[ValidatedStep] = []
        for step in plan.steps:
            spec = self._registry.require(step.tool)  # UnknownToolError if missing

            if allowed_tools is not None and step.tool not in allowed_tools:
                raise ToolNotAllowedError(f"tool not allowed for this caller: {step.tool}")

            for required in spec.required_args:
                if required not in step.arguments:
                    raise ArgumentError(
                        f"step {step.id}: {step.tool} requires argument '{required}'"
                    )

            resolved = spec.risk
            if resolved in self._config.disabled_tiers:
                raise PolicyError(
                    f"step {step.id}: {step.tool} is a {resolved.value} action, disabled by policy"
                )

            validated.append(
                ValidatedStep(
                    id=step.id,
                    tool=step.tool,
                    arguments=dict(step.arguments),
                    depends_on=list(step.depends_on),
                    reason=step.reason,
                    resolved_risk=resolved,
                    claimed_risk=step.claimed_risk,
                    risk_mismatch=step.claimed_risk is not None and step.claimed_risk != resolved,
                    decision=self._config.decide(resolved),
                )
            )

        return ValidatedPlan(summary=plan.summary, steps=validated)


def _reject_dependency_cycles(plan: Plan) -> None:
    """Raise if the steps' ``depends_on`` graph contains a cycle or self-loop.

    A cyclic plan would pass every per-step check yet never become runnable —
    each step waits forever on another — wedging the workflow. We refuse it at
    validation instead. Assumes every ``depends_on`` id has already been checked
    to exist.
    """
    graph = {step.id: list(step.depends_on) for step in plan.steps}
    # DFS three-colouring: 0=unvisited, 1=on current path, 2=done.
    color: dict[str, int] = dict.fromkeys(graph, 0)

    def visit(node: str) -> None:
        color[node] = 1
        for dep in graph[node]:
            if color[dep] == 1:
                raise PlanValidationError(f"plan has a dependency cycle involving step '{node}'")
            if color[dep] == 0:
                visit(dep)
        color[node] = 2

    for step_id in graph:
        if color[step_id] == 0:
            visit(step_id)
