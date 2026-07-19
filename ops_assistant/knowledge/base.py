"""A small, dependency-free knowledge base.

Markdown files are split into section chunks and retrieved by TF-IDF — enough to
give the assistant corporate context (return rules, SLA, templates) with a
citation on every result, without pulling in an embedding stack. Swapping in
pgvector + embeddings is a documented upgrade behind the same ``search`` interface.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path

_TOKEN = re.compile(r"[a-z0-9]+")
_HEADER = re.compile(r"^#{1,6}\s+(.*)$")

# Common words carry little signal and, being frequent, distort the ranking.
_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "is",
        "it",
        "its",
        "may",
        "might",
        "must",
        "my",
        "of",
        "on",
        "or",
        "our",
        "should",
        "the",
        "their",
        "them",
        "these",
        "this",
        "those",
        "to",
        "us",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "within",
        "would",
        "you",
        "your",
    ]
)


@dataclass(frozen=True)
class Chunk:
    text: str
    source: str
    section: str
    score: float = 0.0


def _tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS]


def _split_sections(markdown: str, source: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    header: str | None = None
    body: list[str] = []

    def flush() -> None:
        text = "\n".join(body).strip()
        if text:
            chunks.append(Chunk(text=text, source=source, section=header or "General"))

    for line in markdown.splitlines():
        match = _HEADER.match(line)
        if match:
            flush()
            header = match.group(1).strip()
            body = []
        else:
            body.append(line)
    flush()
    return chunks


class KnowledgeBase:
    def __init__(self, chunks: list[Chunk]) -> None:
        self._chunks = chunks
        # Index the section header along with the body so a query matching the
        # heading (e.g. "returns") ranks that section.
        self._term_counts = [Counter(_tokenize(f"{c.section} {c.text}")) for c in chunks]
        n = len(chunks)
        document_freq: Counter[str] = Counter()
        for counts in self._term_counts:
            document_freq.update(counts.keys())
        self._idf = {term: math.log((n + 1) / (df + 1)) + 1.0 for term, df in document_freq.items()}

    @property
    def chunks(self) -> list[Chunk]:
        return self._chunks

    @classmethod
    def from_directory(cls, path: str) -> KnowledgeBase:
        chunks: list[Chunk] = []
        for md in sorted(Path(path).glob("*.md")):
            chunks.extend(_split_sections(md.read_text(encoding="utf-8"), md.name))
        return cls(chunks)

    def search(self, query: str, k: int = 3) -> list[Chunk]:
        query_terms = set(_tokenize(query))
        if not query_terms:
            return []
        scored: list[Chunk] = []
        for chunk, counts in zip(self._chunks, self._term_counts, strict=True):
            score = sum(counts.get(term, 0) * self._idf.get(term, 0.0) for term in query_terms)
            if score > 0:
                scored.append(replace(chunk, score=round(score, 4)))
        scored.sort(key=lambda c: c.score, reverse=True)
        return scored[:k]
