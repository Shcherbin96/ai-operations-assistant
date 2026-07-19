"""Core data contracts.

These are the planner-facing types: the language model returns a :class:`Plan`
made of :class:`PlanStep` objects, and nothing else. The model may attach a
``claimed_risk`` to a step, but that label is *advisory only* — the policy engine
re-derives the real risk tier server-side and never trusts the model's word.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

# Canonical risk order, lowest to highest. The index in this tuple is the tier's
# rank, so tiers are comparable without hard-coding numbers on the enum.
_RISK_ORDER: tuple[str, ...] = (
    "read_only",
    "draft",
    "write",
    "external_side_effect",
    "destructive",
)


class RiskTier(StrEnum):
    """How dangerous a tool is. The server owns this classification, not the model."""

    READ_ONLY = "read_only"
    DRAFT = "draft"
    WRITE = "write"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"
    DESTRUCTIVE = "destructive"

    @property
    def rank(self) -> int:
        """Position in the canonical order; higher means more dangerous."""
        return _RISK_ORDER.index(self.value)


class WorkflowStatus(StrEnum):
    """Lifecycle of a single request-to-completion workflow."""

    CREATED = "created"
    PLANNED = "planned"
    VALIDATING = "validating"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    EXECUTING = "executing"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(StrEnum):
    """Lifecycle of a single step inside a workflow."""

    PENDING = "pending"
    BLOCKED = "blocked"
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    REJECTED = "rejected"


class OperationRequest(BaseModel):
    """A user's plain-language request, captured verbatim. Immutable once created."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    text: str
    user: str
    source: str


class PlanStep(BaseModel):
    """One proposed action. ``claimed_risk`` is the model's advisory guess only."""

    model_config = ConfigDict(extra="forbid")

    id: str
    tool: str
    arguments: dict[str, object] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    reason: str = ""
    claimed_risk: RiskTier | None = None


class Plan(BaseModel):
    """The complete structured proposal returned by a planner."""

    model_config = ConfigDict(extra="forbid")

    summary: str
    requires_clarification: bool = False
    clarification_question: str | None = None
    steps: list[PlanStep] = Field(default_factory=list)


def plan_fingerprint(plan: Plan) -> str:
    """Stable identity of *what a plan actually does*.

    Deliberately excludes ``claimed_risk`` (advisory, not part of the action) and
    ``reason`` (prose). Two plans with identical steps but different self-reported
    risk labels fingerprint the same, so an approval binds to the real actions —
    a relabel cannot silently invalidate it, and a changed action always does.
    """
    material = {
        "summary": plan.summary,
        "steps": [
            {
                "id": s.id,
                "tool": s.tool,
                "arguments": s.arguments,
                "depends_on": s.depends_on,
            }
            for s in plan.steps
        ],
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
