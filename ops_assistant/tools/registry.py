"""The tool registry: the server's own source of truth for what a tool *is*.

Crucially, a tool's :class:`~ops_assistant.models.RiskTier` lives here, on the
server, keyed by tool name — never taken from the plan the model returns. This is
what makes "the model cannot lower its own risk" enforceable rather than a
promise.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from ops_assistant.errors import UnknownToolError
from ops_assistant.models import RiskTier

ToolHandler = Callable[[Mapping[str, object]], object]


@dataclass(frozen=True)
class ToolSpec:
    """A registered tool: its canonical risk, its handler, and its required args."""

    name: str
    risk: RiskTier
    description: str
    handler: ToolHandler
    required_args: tuple[str, ...] = field(default=())


class ToolRegistry:
    """An in-memory set of tools, keyed by name."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"tool already registered: {spec.name}")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def require(self, name: str) -> ToolSpec:
        spec = self._tools.get(name)
        if spec is None:
            raise UnknownToolError(f"unknown tool: {name}")
        return spec

    def names(self) -> frozenset[str]:
        return frozenset(self._tools)

    def __contains__(self, name: object) -> bool:
        return name in self._tools
