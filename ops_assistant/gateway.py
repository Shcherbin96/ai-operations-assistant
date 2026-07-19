"""The tool gateway: the *only* component that actually calls a tool.

Everything reaches external effects through here, so this is where idempotency and
tool-level auditing live. An ``idempotency_key`` that has already completed returns
the stored result without invoking the handler again — a retried request, or
tapping *Approve* twice within a process, cannot run the tool twice.

Where executed results are remembered is an :class:`IdempotencyStore` (in memory
here, a Postgres ``tool_executions`` table keyed on the idempotency key in Stage 2,
so a completed execution is remembered across restarts). Cross-process, a given
approved step reaches this method exactly once because the upstream approval
decision is a compare-and-set (see ``approval.py``); the residual window — a crash
*between* the handler firing and the result being recorded — is not yet closed by a
reserve-before-execute, and is noted as future work.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from ops_assistant.audit import AuditEventType, AuditStore
from ops_assistant.errors import ToolExecutionError
from ops_assistant.tools.registry import ToolRegistry


class ToolResult:
    """Outcome of one tool execution. ``replayed`` marks an idempotent cache hit."""

    __slots__ = ("tool", "output", "replayed")

    def __init__(self, tool: str, output: object, replayed: bool) -> None:
        self.tool = tool
        self.output = output
        self.replayed = replayed


class IdempotencyStore(Protocol):
    def get(self, key: str) -> ToolResult | None: ...

    def put(self, key: str, *, workflow_id: str, step_id: str, result: ToolResult) -> None: ...


class InMemoryIdempotencyStore:
    def __init__(self) -> None:
        self._results: dict[str, ToolResult] = {}

    def get(self, key: str) -> ToolResult | None:
        return self._results.get(key)

    def put(self, key: str, *, workflow_id: str, step_id: str, result: ToolResult) -> None:
        self._results[key] = result


class ToolGateway:
    def __init__(
        self,
        registry: ToolRegistry,
        audit: AuditStore,
        idempotency: IdempotencyStore | None = None,
    ) -> None:
        self._registry = registry
        self._audit = audit
        self._idem: IdempotencyStore = idempotency or InMemoryIdempotencyStore()

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

        cached = self._idem.get(idempotency_key)
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
        self._idem.put(idempotency_key, workflow_id=workflow_id, step_id=step_id, result=result)
        self._audit.append(
            workflow_id,
            AuditEventType.TOOL_SUCCEEDED,
            actor=actor,
            step_id=step_id,
            payload={"tool": tool},
        )
        return result
