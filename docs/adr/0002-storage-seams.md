# ADR-0002 — Storage seams: protocols with in-memory + Postgres

**Status:** accepted (supersedes `docs/design/stage-2-persistence.md`)

## Context

The system needs durable state (a paused workflow must survive a restart), but the
core control logic — validation, approval, execution — shouldn't be entangled with a
database. Coupling the orchestrator to an ORM would make the safety-critical logic
hard to test and hard to reason about, and would force a DB on the keyless demo.

## Decision

Each persistence concern is a **`Protocol`** — `WorkflowStore`, `ApprovalStore`,
`AuditStore`, `IdempotencyStore` — with two implementations: in-memory and Postgres
(`persistence/`). The core (`service.py`) imports nothing from `persistence`;
`factory.py` wires the Postgres stores when `OPS_DATABASE_URL` is set, otherwise the
in-memory ones. Concurrency/consistency guarantees live *in* the seam: append-only
audit via triggers, idempotency via `ON CONFLICT`, single-use approval via
compare-and-set, optimistic locking via a `version` column.

## Consequences

- The entire control machinery is unit-tested with zero I/O; the same suite of
  behaviors is re-verified against real Postgres in the integration job.
- The keyless demo runs the full thesis with no database at all.
- Swapping/adding a backend is local to the seam.
- Cost: two implementations to keep in step — mitigated by testing both against the
  same behavioral contract.
- Not done: a unit-of-work spanning stores (each opens its own transaction), so a
  crash mid-approve is a known, documented edge (see the gateway/service comments).
