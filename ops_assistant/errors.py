"""Typed error hierarchy.

Every failure the system can produce has a specific type and a stable ``code``,
so callers (and the API layer) can branch on the cause instead of parsing
messages. User-facing messages never carry secrets; diagnostic detail stays in
logs.
"""

from __future__ import annotations

from enum import StrEnum


class ErrorCode(StrEnum):
    VALIDATION = "validation_error"
    UNKNOWN_TOOL = "unknown_tool"
    TOOL_NOT_ALLOWED = "tool_not_allowed"
    ARGUMENT = "argument_error"
    POLICY = "policy_error"
    PLANNING = "planning_error"
    APPROVAL_NOT_FOUND = "approval_not_found"
    APPROVAL_EXPIRED = "approval_expired"
    APPROVAL_ALREADY_DECIDED = "approval_already_decided"
    PLAN_CHANGED = "plan_changed"
    STATE_TRANSITION = "state_transition_error"
    TOOL_EXECUTION = "tool_execution_error"
    DUPLICATE_EXECUTION = "duplicate_execution"
    NOT_FOUND = "not_found"


class OpsAssistantError(Exception):
    """Base class for every domain error. Carries a stable :class:`ErrorCode`."""

    code: ErrorCode = ErrorCode.VALIDATION

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class PlanValidationError(OpsAssistantError):
    code = ErrorCode.VALIDATION


class UnknownToolError(OpsAssistantError):
    code = ErrorCode.UNKNOWN_TOOL


class ToolNotAllowedError(OpsAssistantError):
    code = ErrorCode.TOOL_NOT_ALLOWED


class ArgumentError(OpsAssistantError):
    code = ErrorCode.ARGUMENT


class PolicyError(OpsAssistantError):
    code = ErrorCode.POLICY


class PlanningError(OpsAssistantError):
    code = ErrorCode.PLANNING


class ApprovalNotFoundError(OpsAssistantError):
    code = ErrorCode.APPROVAL_NOT_FOUND


class ApprovalExpiredError(OpsAssistantError):
    code = ErrorCode.APPROVAL_EXPIRED


class ApprovalAlreadyDecidedError(OpsAssistantError):
    code = ErrorCode.APPROVAL_ALREADY_DECIDED


class PlanChangedError(OpsAssistantError):
    """An approval was issued against a plan that has since changed."""

    code = ErrorCode.PLAN_CHANGED


class StateTransitionError(OpsAssistantError):
    code = ErrorCode.STATE_TRANSITION


class ToolExecutionError(OpsAssistantError):
    code = ErrorCode.TOOL_EXECUTION


class DuplicateExecutionError(OpsAssistantError):
    code = ErrorCode.DUPLICATE_EXECUTION


class NotFoundError(OpsAssistantError):
    code = ErrorCode.NOT_FOUND
