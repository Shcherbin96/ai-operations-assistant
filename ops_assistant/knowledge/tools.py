"""The ``knowledge.search`` tool: read-only retrieval of cited policy snippets."""

from __future__ import annotations

from collections.abc import Mapping

from ops_assistant.errors import ArgumentError
from ops_assistant.knowledge.base import KnowledgeBase
from ops_assistant.models import RiskTier
from ops_assistant.tools.registry import ToolSpec


def build_knowledge_tool(kb: KnowledgeBase) -> ToolSpec:
    def search(args: Mapping[str, object]) -> object:
        if "query" not in args:
            raise ArgumentError("missing required argument: query")
        raw_k = args.get("k", 3)
        k = int(raw_k) if isinstance(raw_k, int | str) else 3
        return [
            {"text": c.text, "source": c.source, "section": c.section, "score": c.score}
            for c in kb.search(str(args["query"]), k=k)
        ]

    return ToolSpec(
        "knowledge.search",
        RiskTier.READ_ONLY,
        "Search the company knowledge base; returns policy snippets with citations",
        search,
        ("query",),
    )
