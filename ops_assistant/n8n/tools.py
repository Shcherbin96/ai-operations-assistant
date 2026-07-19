"""The ``n8n.run`` tool: trigger an allowlisted n8n workflow, gated by approval."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from ops_assistant.errors import ArgumentError
from ops_assistant.models import RiskTier
from ops_assistant.n8n.client import N8nClient
from ops_assistant.tools.registry import ToolSpec


def build_n8n_tool(client: N8nClient, allowed_workflows: Iterable[str]) -> ToolSpec:
    allowed = frozenset(allowed_workflows)

    def run(args: Mapping[str, object]) -> object:
        if "workflow" not in args:
            raise ArgumentError("missing required argument: workflow")
        workflow = str(args["workflow"])
        if workflow not in allowed:
            raise ArgumentError(f"n8n workflow not allowed: {workflow}")
        payload = args.get("payload", {})
        return client.trigger(workflow, payload if isinstance(payload, dict) else {})

    return ToolSpec(
        "n8n.run",
        RiskTier.EXTERNAL_SIDE_EFFECT,
        "Run an allowlisted n8n workflow via a signed webhook",
        run,
        ("workflow",),
    )
