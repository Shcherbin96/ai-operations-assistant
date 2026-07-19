"""The planner boundary.

A planner turns a plain-language request into a structured :class:`Plan`. It has
*no* access to tools, secrets, or the network beyond producing the plan — it is a
proposer, nothing more. Everything downstream treats its output as untrusted.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ops_assistant.models import OperationRequest, Plan


@runtime_checkable
class Planner(Protocol):
    def plan(self, request: OperationRequest) -> Plan:
        """Propose a structured plan for the request. Never executes anything."""
        ...
