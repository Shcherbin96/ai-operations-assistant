"""Knowledge base: TF-IDF retrieval over markdown, with citations."""

from pathlib import Path

import pytest

from ops_assistant.errors import ArgumentError
from ops_assistant.knowledge.base import Chunk, KnowledgeBase
from ops_assistant.knowledge.tools import build_knowledge_tool
from ops_assistant.models import RiskTier


def _kb() -> KnowledgeBase:
    return KnowledgeBase(
        [
            Chunk(
                text="Returns are accepted within 30 days with a receipt.",
                source="returns.md",
                section="Returns",
            ),
            Chunk(
                text="Standard shipping takes 3 to 5 business days.",
                source="shipping.md",
                section="Shipping",
            ),
            Chunk(
                text="Reply to every customer email within one business day.",
                source="sla.md",
                section="SLA",
            ),
        ]
    )


def test_search_ranks_the_relevant_chunk_first() -> None:
    results = _kb().search("what is the refund and returns window?", k=2)
    assert results
    assert results[0].source == "returns.md"
    assert results[0].score > 0


def test_search_returns_citations() -> None:
    top = _kb().search("shipping time", k=1)[0]
    assert top.source == "shipping.md"
    assert top.section == "Shipping"


def test_search_with_no_overlap_returns_nothing() -> None:
    assert _kb().search("quantum astrophysics", k=3) == []


def test_empty_or_punctuation_query_returns_nothing() -> None:
    assert _kb().search("", k=3) == []
    assert _kb().search("!!! ???", k=3) == []


def test_search_respects_k() -> None:
    assert len(_kb().search("email shipping returns", k=1)) == 1


def test_from_directory_loads_sections_as_chunks(tmp_path: Path) -> None:
    (tmp_path / "policy.md").write_text(
        "# Returns\nReturns within 30 days.\n\n# Warranty\nOne year warranty on defects.\n"
    )
    kb = KnowledgeBase.from_directory(str(tmp_path))
    sections = sorted(c.section for c in kb.chunks)
    assert sections == ["Returns", "Warranty"]
    warranty = kb.search("warranty defect", k=1)[0]
    assert warranty.section == "Warranty"
    assert warranty.source == "policy.md"


def test_from_empty_directory_is_safe(tmp_path: Path) -> None:
    kb = KnowledgeBase.from_directory(str(tmp_path))
    assert kb.chunks == []
    assert kb.search("anything", k=3) == []


def test_knowledge_tool_is_read_only_and_returns_citations() -> None:
    spec = build_knowledge_tool(_kb())
    assert spec.risk is RiskTier.READ_ONLY
    assert spec.required_args == ("query",)
    results = spec.handler({"query": "returns refund window"})
    assert isinstance(results, list) and results
    assert results[0]["source"] == "returns.md"
    assert set(results[0]) == {"text", "source", "section", "score"}


def test_knowledge_tool_requires_a_query() -> None:
    spec = build_knowledge_tool(_kb())
    with pytest.raises(ArgumentError):
        spec.handler({})
