"""In-flight workflow state: the mutable aggregate the orchestrator drives.

These records live in their own module so both the service and the storage layer
(Stage 2) can share them without a circular import. A :class:`WorkflowRecord` is
the unit of persistence — a store loads it, the service mutates it, the store
saves it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ops_assistant.models import OperationRequest, StepStatus, WorkflowStatus
from ops_assistant.policy import ValidatedStep


@dataclass
class StepRecord:
    validated: ValidatedStep
    status: StepStatus = StepStatus.PENDING
    output: object | None = None
    approval_id: str | None = None
    error: str | None = None


@dataclass
class WorkflowRecord:
    id: str
    request: OperationRequest
    status: WorkflowStatus
    summary: str = ""
    requires_clarification: bool = False
    clarification_question: str | None = None
    plan_fingerprint: str = ""
    version: int = 0
    steps: list[StepRecord] = field(default_factory=list)


def is_empty(output: object) -> bool:
    return output is None or (isinstance(output, (list, tuple, dict, str)) and len(output) == 0)
