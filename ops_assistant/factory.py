"""Wiring: build an :class:`OpsService` backed by Postgres.

Kept separate from ``service.py`` so the core has no hard dependency on
SQLAlchemy — the in-memory service imports nothing from ``persistence``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from ops_assistant.config import Settings
from ops_assistant.persistence.postgres import (
    PostgresApprovalStore,
    PostgresAuditStore,
    PostgresIdempotencyStore,
    PostgresWorkflowStore,
)
from ops_assistant.persistence.schema import create_schema
from ops_assistant.service import OpsService, _utcnow


def build_engine(database_url: str) -> Engine:
    """Create a SQLAlchemy engine, normalising the URL to the psycopg v3 driver."""
    if database_url.startswith("postgresql://"):
        database_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return create_engine(database_url, pool_pre_ping=True)


def make_postgres_service(
    engine: Engine, *, clock: Callable[[], datetime] = _utcnow, **kwargs: Any
) -> OpsService:
    """An OpsService whose workflows, approvals, and audit live in Postgres."""
    return OpsService(
        clock=clock,
        store=PostgresWorkflowStore(engine),
        approval_store=PostgresApprovalStore(engine),
        audit_store=PostgresAuditStore(engine, clock),
        idempotency_store=PostgresIdempotencyStore(engine, clock),
        **kwargs,
    )


def service_from_settings(settings: Settings) -> OpsService:  # pragma: no cover - wiring
    """Build a Postgres-backed service if OPS_DATABASE_URL is set, else in-memory."""
    if settings.database_url:
        engine = build_engine(settings.database_url)
        create_schema(engine)
        return make_postgres_service(engine)
    return OpsService()
