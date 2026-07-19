"""Postgres schema.

Tables for the five persisted concerns, plus the two guarantees that are best
enforced by the database itself:

* ``audit_events`` is **append-only** — a trigger raises on any UPDATE or DELETE,
  so history cannot be rewritten even by a bug or a direct SQL statement.
* ``tool_executions`` has the ``idempotency_key`` as its primary key, so a repeated
  execution is an ``INSERT ... ON CONFLICT DO NOTHING`` — a no-op at the database
  level, not just in process memory.

Optimistic locking lives on ``workflows.version`` (see the Postgres stores).
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine

metadata = MetaData()

workflows = Table(
    "workflows",
    metadata,
    Column("id", String, primary_key=True),
    Column("status", String, nullable=False),
    Column("summary", Text, nullable=False),
    Column("requires_clarification", Boolean, nullable=False),
    Column("clarification_question", Text, nullable=True),
    Column("plan_fingerprint", String, nullable=False),
    Column("version", Integer, nullable=False),
    Column("request", JSONB, nullable=False),
)

steps = Table(
    "steps",
    metadata,
    Column("workflow_id", String, ForeignKey("workflows.id", ondelete="CASCADE"), primary_key=True),
    Column("step_id", String, primary_key=True),
    Column("ordinal", Integer, nullable=False),
    Column("validated", JSONB, nullable=False),
    Column("status", String, nullable=False),
    Column("output", JSONB, nullable=True),
    Column("approval_id", String, nullable=True),
    Column("error", Text, nullable=True),
)

approvals = Table(
    "approvals",
    metadata,
    Column("id", String, primary_key=True),
    Column("workflow_id", String, nullable=False, index=True),
    Column("step_id", String, nullable=False),
    Column("plan_fingerprint", String, nullable=False),
    Column("tool", String, nullable=False),
    Column("arguments", JSONB, nullable=False),
    Column("risk", String, nullable=False),
    Column("status", String, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("decided_by", String, nullable=True),
    Column("decided_at", DateTime(timezone=True), nullable=True),
    Column("decision_reason", Text, nullable=True),
)

audit_events = Table(
    "audit_events",
    metadata,
    Column("seq", BigInteger, Identity(always=True), primary_key=True),
    Column("workflow_id", String, nullable=False, index=True),
    Column("step_id", String, nullable=True),
    Column("event_type", String, nullable=False),
    Column("actor", String, nullable=False),
    Column("timestamp", DateTime(timezone=True), nullable=False),
    Column("correlation_id", String, nullable=True),
    Column("payload", JSONB, nullable=False),
)

tool_executions = Table(
    "tool_executions",
    metadata,
    Column("idempotency_key", String, primary_key=True),
    Column("workflow_id", String, nullable=False),
    Column("step_id", String, nullable=False),
    Column("tool", String, nullable=False),
    Column("output", JSONB, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

# BEFORE UPDATE OR DELETE trigger: audit history is immutable at the DB layer.
_APPEND_ONLY_DDL = """
CREATE OR REPLACE FUNCTION audit_events_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_events is append-only (% is not permitted)', TG_OP;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_events_no_mutate ON audit_events;
CREATE TRIGGER audit_events_no_mutate
    BEFORE UPDATE OR DELETE ON audit_events
    FOR EACH ROW EXECUTE FUNCTION audit_events_append_only();
"""


def create_schema(engine: Engine) -> None:
    """Create the tables and install the append-only audit trigger (idempotent)."""
    metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text(_APPEND_ONLY_DDL))
