# ADR-0005 — Single-process locking; schema via create_all

**Status:** accepted

## Context

Two operational choices a reviewer will (rightly) probe: how concurrent requests
are serialized, and how the database schema is managed. Both were made for
obviously-correct simplicity at portfolio scale, with a clear upgrade path — worth
stating plainly rather than leaving implicit.

## Decision

**Concurrency.** The orchestrator holds a single process-wide `RLock` around state
mutations. Cross-process correctness does not rely on it — it rests on the database
(optimistic `version` locking → `ConflictError`, and compare-and-set on approvals).
The lock is held across planning (an LLM call) and execution (tool I/O), so
unrelated workflows serialize behind a slow provider.

**Schema.** Tables are created with `create_all` on startup (`persistence/schema.py`),
not via a migration tool.

## Consequences

- The locking is trivially correct and easy to reason about; the cost is throughput
  under a slow provider. **Upgrade path:** per-workflow locks, or releasing the lock
  during the (side-effect-free) planning call. Not done because it is a real refactor
  of the core orchestration path with genuine race risk, unjustified at this scale.
- `create_all` is fine for a fresh deployment and the test/demo flow. **Upgrade
  path:** Alembic migrations for schema evolution on a live database. (The Stage-2
  design note anticipated Alembic; this ADR supersedes that intent with the
  as-built choice.)
- Both are documented rather than built — a deliberate "known limitation + upgrade
  path", not an oversight.
