"""Tool gateway: the single execution chokepoint — idempotent and fully audited."""

import pytest

from ops_assistant.audit import AuditEventType, AuditLog
from ops_assistant.errors import ToolExecutionError, UnknownToolError
from ops_assistant.gateway import ToolGateway
from ops_assistant.models import RiskTier
from ops_assistant.tools.registry import ToolRegistry, ToolSpec


def _registry_with_counter() -> tuple[ToolRegistry, list[int]]:
    calls: list[int] = []

    def handler(args: object) -> str:
        calls.append(1)
        return "done"

    reg = ToolRegistry()
    reg.register(ToolSpec("x.do", RiskTier.WRITE, "d", handler))
    return reg, calls


def test_execute_runs_the_handler_and_returns_output() -> None:
    reg, _ = _registry_with_counter()
    gw = ToolGateway(reg, AuditLog())
    result = gw.execute("wf1", "s1", "x.do", {}, idempotency_key="k1")
    assert result.output == "done"
    assert result.replayed is False


def test_execute_writes_called_and_succeeded_audit_events() -> None:
    reg, _ = _registry_with_counter()
    audit = AuditLog()
    gw = ToolGateway(reg, audit)
    gw.execute("wf1", "s1", "x.do", {}, idempotency_key="k1")
    types = [e.event_type for e in audit.for_workflow("wf1")]
    assert AuditEventType.TOOL_CALLED in types
    assert AuditEventType.TOOL_SUCCEEDED in types


def test_duplicate_idempotency_key_does_not_run_the_handler_again() -> None:
    reg, calls = _registry_with_counter()
    gw = ToolGateway(reg, AuditLog())
    first = gw.execute("wf1", "s1", "x.do", {}, idempotency_key="k1")
    second = gw.execute("wf1", "s1", "x.do", {}, idempotency_key="k1")
    assert sum(calls) == 1  # handler ran exactly once
    assert first.output == second.output
    assert second.replayed is True


def test_handler_failure_is_wrapped_and_audited() -> None:
    def boom(args: object) -> str:
        raise RuntimeError("upstream exploded")

    reg = ToolRegistry()
    reg.register(ToolSpec("x.boom", RiskTier.WRITE, "d", boom))
    audit = AuditLog()
    gw = ToolGateway(reg, audit)
    with pytest.raises(ToolExecutionError):
        gw.execute("wf1", "s1", "x.boom", {}, idempotency_key="k1")
    assert AuditEventType.TOOL_FAILED in [e.event_type for e in audit.for_workflow("wf1")]


def test_unknown_tool_raises() -> None:
    gw = ToolGateway(ToolRegistry(), AuditLog())
    with pytest.raises(UnknownToolError):
        gw.execute("wf1", "s1", "nope", {}, idempotency_key="k1")
