"""Guarded state machines for workflows and their steps.

Transitions are explicit and one-directional: you cannot move a ``completed``
workflow back to ``executing``, and you cannot re-run a ``succeeded`` step. Any
illegal move raises :class:`StateTransitionError` instead of silently corrupting
state.
"""

from __future__ import annotations

from ops_assistant.errors import StateTransitionError
from ops_assistant.models import StepStatus, WorkflowStatus

WORKFLOW_TRANSITIONS: dict[WorkflowStatus, frozenset[WorkflowStatus]] = {
    WorkflowStatus.CREATED: frozenset(
        {WorkflowStatus.PLANNED, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED}
    ),
    WorkflowStatus.PLANNED: frozenset(
        {WorkflowStatus.VALIDATING, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED}
    ),
    WorkflowStatus.VALIDATING: frozenset(
        {
            WorkflowStatus.AWAITING_APPROVAL,
            WorkflowStatus.APPROVED,
            WorkflowStatus.EXECUTING,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
        }
    ),
    WorkflowStatus.AWAITING_APPROVAL: frozenset(
        {
            WorkflowStatus.APPROVED,
            WorkflowStatus.REJECTED,
            WorkflowStatus.CANCELLED,
            WorkflowStatus.FAILED,
        }
    ),
    WorkflowStatus.APPROVED: frozenset(
        {WorkflowStatus.EXECUTING, WorkflowStatus.FAILED, WorkflowStatus.CANCELLED}
    ),
    WorkflowStatus.EXECUTING: frozenset(
        {
            WorkflowStatus.COMPLETED,
            WorkflowStatus.AWAITING_APPROVAL,
            WorkflowStatus.FAILED,
            WorkflowStatus.CANCELLED,
        }
    ),
    WorkflowStatus.COMPLETED: frozenset(),
    WorkflowStatus.REJECTED: frozenset(),
    WorkflowStatus.FAILED: frozenset(),
    WorkflowStatus.CANCELLED: frozenset(),
}

STEP_TRANSITIONS: dict[StepStatus, frozenset[StepStatus]] = {
    StepStatus.PENDING: frozenset(
        {
            StepStatus.AWAITING_APPROVAL,
            StepStatus.RUNNING,
            StepStatus.SKIPPED,
            StepStatus.REJECTED,
        }
    ),
    StepStatus.AWAITING_APPROVAL: frozenset(
        {StepStatus.RUNNING, StepStatus.REJECTED, StepStatus.SKIPPED}
    ),
    StepStatus.RUNNING: frozenset({StepStatus.SUCCEEDED, StepStatus.FAILED}),
    StepStatus.SUCCEEDED: frozenset(),
    StepStatus.FAILED: frozenset(),
    StepStatus.SKIPPED: frozenset(),
    StepStatus.REJECTED: frozenset(),
}


def is_terminal_workflow(status: WorkflowStatus) -> bool:
    return not WORKFLOW_TRANSITIONS[status]


def assert_workflow_transition(frm: WorkflowStatus, to: WorkflowStatus) -> None:
    if to not in WORKFLOW_TRANSITIONS[frm]:
        raise StateTransitionError(f"illegal workflow transition: {frm.value} -> {to.value}")


def assert_step_transition(frm: StepStatus, to: StepStatus) -> None:
    if to not in STEP_TRANSITIONS[frm]:
        raise StateTransitionError(f"illegal step transition: {frm.value} -> {to.value}")
