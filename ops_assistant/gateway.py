"""The tool gateway: the *only* component that actually calls a tool.

Everything reaches external effects through here, so this is where idempotency and
tool-level auditing live. An ``idempotency_key`` that has already run returns the
stored result without invoking the handler again — tapping *Approve* twice, or a
retried request, cannot send an email or create an event twice.
"""

from __future__ import annotations

from collections.abc import Mapping

from ops_assistant.audit import AuditEventType, AuditLog
from ops_assistant.errors import ToolExecutionError
from ops_assistant.tools.registry import ToolRegistry


class ToolResult:
    """Outcome of one tool execution. ``replayed`` marks an idempotent cache hit."""

    __slots__ = ("tool", "output", "replayed")

    def __init__(self, tool: str, output: object, replayed: bool) -> None:
        self.tool = tool
        self.output = output
        self.replayed = replayed


class ToolGateway:
    def __init__(self, registry: ToolRegistry, audit: AuditLog) -> None:
        self._registry = registry
        self._audit = audit
        self._results: dict[str, ToolResult] = {}

    def execute(
        self,
        workflow_id: str,
        step_id: str,
        tool: str,
        arguments: Mapping[str, object],
        *,
        idempotency_key: str,
        actor: str = "gateway",
    ) -> ToolResult:
        spec = self._registry.require(tool)  # UnknownToolError if missing

        cached = self._results.get(idempotency_key)
        if cached is not None:
            return ToolResult(tool=cached.tool, output=cached.output, replayed=True)

        self._audit.append(
            workflow_id,
            AuditEventType.TOOL_CALLED,
            actor=actor,
            step_id=step_id,
            payload={"tool": tool, "idempotency_key": idempotency_key},
        )

        try:
            output = spec.handler(arguments)
        except Exception as exc:
            self._audit.append(
                workflow_id,
                AuditEventType.TOOL_FAILED,
                actor=actor,
                step_id=step_id,
                payload={"tool": tool, "error": type(exc).__name__},
            )
            raise ToolExecutionError(f"tool {tool} failed: {exc}") from exc

        result = ToolResult(tool=tool, output=output, replayed=False)
        self._results[idempotency_key] = result
        self._audit.append(
            workflow_id,
            AuditEventType.TOOL_SUCCEEDED,
            actor=actor,
            step_id=step_id,
            payload={"tool": tool},
        )
        return result
