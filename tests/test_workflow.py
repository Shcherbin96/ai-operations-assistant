"""Workflow / step state machines — transitions are guarded, terminals are final."""

import pytest

from ops_assistant.errors import StateTransitionError
from ops_assistant.models import StepStatus, WorkflowStatus
from ops_assistant.workflow import (
    assert_step_transition,
    assert_workflow_transition,
    is_terminal_workflow,
)


def test_legal_workflow_transition_is_allowed() -> None:
    assert_workflow_transition(WorkflowStatus.CREATED, WorkflowStatus.PLANNED)
    assert_workflow_transition(WorkflowStatus.AWAITING_APPROVAL, WorkflowStatus.APPROVED)
    assert_workflow_transition(WorkflowStatus.EXECUTING, WorkflowStatus.COMPLETED)


def test_illegal_workflow_transition_raises() -> None:
    with pytest.raises(StateTransitionError):
        assert_workflow_transition(WorkflowStatus.CREATED, WorkflowStatus.COMPLETED)


def test_completed_is_terminal_and_cannot_reopen() -> None:
    assert is_terminal_workflow(WorkflowStatus.COMPLETED)
    with pytest.raises(StateTransitionError):
        assert_workflow_transition(WorkflowStatus.COMPLETED, WorkflowStatus.EXECUTING)


def test_rejected_and_failed_are_terminal() -> None:
    assert is_terminal_workflow(WorkflowStatus.REJECTED)
    assert is_terminal_workflow(WorkflowStatus.FAILED)
    assert is_terminal_workflow(WorkflowStatus.CANCELLED)
    assert not is_terminal_workflow(WorkflowStatus.EXECUTING)


def test_legal_step_transition_is_allowed() -> None:
    assert_step_transition(StepStatus.PENDING, StepStatus.RUNNING)
    assert_step_transition(StepStatus.AWAITING_APPROVAL, StepStatus.RUNNING)
    assert_step_transition(StepStatus.RUNNING, StepStatus.SUCCEEDED)


def test_illegal_step_transition_raises() -> None:
    with pytest.raises(StateTransitionError):
        assert_step_transition(StepStatus.SUCCEEDED, StepStatus.RUNNING)
