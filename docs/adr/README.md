# Architecture Decision Records

Short records of the load-bearing decisions and *why* they were made — the ones a
reviewer would probe.

- [0001 — The server owns risk; the model cannot lower it](0001-server-owns-risk.md)
- [0002 — Storage seams: protocols with in-memory + Postgres](0002-storage-seams.md)
- [0003 — Data-flow references must be declared dependencies](0003-dataflow-declared-deps.md)
- [0004 — Append-only audit + provenance-aware redaction](0004-append-only-audit-and-redaction.md)

Format: Context → Decision → Consequences. The Stage-2 persistence design note
([../design/stage-2-persistence.md](../design/stage-2-persistence.md)) predates this
series; ADR-0002 supersedes it.
