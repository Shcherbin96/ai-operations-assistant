# Stage 2 — Persistence

**Goal:** the system no longer loses state on restart. Workflows, steps, approvals,
audit events, and tool executions live in Postgres, behind the *same* service
interface Stage 1 already exercises — so all 105 existing tests keep passing
unchanged, and a new integration suite proves the Postgres path.

## Principles carried over from Stage 1

- The service API (`submit` / `approve` / `reject` / `get` / `audit_for`) does not
  change. Callers and the FastAPI layer are untouched.
- The append-only audit guarantee becomes a *database* guarantee, not just a
  Python one — enforced by a trigger, so even a bug or a direct SQL `UPDATE`
  cannot rewrite history.
- Keyless demo still works: with no `OPS_DATABASE_URL`, the service falls back to
  the in-memory store, so `scripts/demo.py` and the sandbox need no Postgres.

## Approach

**Introduce a storage seam, then implement it twice.** Extract the three pieces of
Stage-1 in-memory state (`_workflows` dict, `ApprovalEngine`'s dict, `AuditLog`'s
list) behind narrow repository protocols:

| Repository | Backs | Key operations |
|---|---|---|
| `WorkflowRepository` | workflow + step aggregate | `create`, `get`, `save` (optimistic) |
| `ApprovalRepository` | approvals | `add`, `get`, `update`, `pending_for_workflow` |
| `AuditSink` | audit events | `append`, `for_workflow`, `events` (append-only) |

Two implementations:

1. **`InMemoryStore`** — the current Stage-1 behaviour, refactored behind the
   protocols. This is a pure refactor: no behaviour change, existing tests are the
   safety net.
2. **`PostgresStore`** — SQLAlchemy 2.0 (sync, `psycopg` v3 driver). Chosen sync
   to match the existing sync service; no async rewrite.

`OpsService` selects the store from config (`OPS_DATABASE_URL`), defaulting to
in-memory.

## Schema (Postgres)

- `workflows(id pk, status, summary, requires_clarification, clarification_question,
  plan_fingerprint, version int, created_at, updated_at)`
- `steps(id, workflow_id fk, ordinal, tool, arguments jsonb, depends_on jsonb,
  resolved_risk, decision, status, risk_mismatch, approval_id, output jsonb, error,
  primary key (workflow_id, id))`
- `approvals(id pk, workflow_id fk, step_id, plan_fingerprint, tool, arguments jsonb,
  risk, status, created_at, expires_at, decided_by, decided_at, decision_reason)`
- `audit_events(seq bigserial pk, workflow_id, step_id, event_type, actor, timestamp,
  correlation_id, payload jsonb)` — **append-only**
- `tool_executions(idempotency_key pk, workflow_id, step_id, tool, output jsonb,
  created_at)` — idempotency: `INSERT ... ON CONFLICT DO NOTHING` makes a repeated
  approve a no-op at the database level.

### Append-only enforcement

```sql
CREATE FUNCTION audit_events_immutable() RETURNS trigger AS $$
BEGIN RAISE EXCEPTION 'audit_events is append-only'; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER audit_no_update BEFORE UPDATE OR DELETE ON audit_events
FOR EACH ROW EXECUTE FUNCTION audit_events_immutable();
```

(Plus a `REVOKE UPDATE, DELETE ON audit_events` from the app role — belt and
braces, mirroring the pattern proven in `mcp-gateway`.)

### Optimistic locking

`workflows.version` starts at 0; every `save` does
`UPDATE ... SET ..., version = version + 1 WHERE id = :id AND version = :expected`.
Zero rows updated ⇒ a concurrent writer won ⇒ raise `ConflictError` (maps to HTTP
409). This replaces the Stage-1 in-process `RLock` for the cross-process case; the
lock stays for the in-memory store.

## Migrations

Alembic, one initial revision creating the tables, the trigger, and the grants.
`alembic upgrade head` runs in the Postgres test fixture and is documented for
local/prod.

## Testing

- **Unit tests (existing 105):** unchanged, run against `InMemoryStore`. No Postgres
  required — CI's lint/test matrix is untouched.
- **Integration tests (new):** spin a real Postgres with `testcontainers`, run the
  same behavioural scenarios against `PostgresStore`, plus persistence-specific
  tests: state survives a fresh service instance, the audit trigger rejects
  `UPDATE`/`DELETE`, idempotent tool execution across two "requests", and an
  optimistic-lock conflict raises. Marked `@pytest.mark.integration`, skipped when
  Docker is absent.
- **CI:** a separate `integration` job on ubuntu (Docker available) runs the marked
  suite; the existing 3-OS unit matrix stays green with no new dependencies on the
  fast path.

## Out of scope for Stage 2

Connection pooling tuning, read replicas, and multi-tenant row scoping — noted for
later, not built now.
