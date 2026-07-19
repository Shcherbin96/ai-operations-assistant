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
from ops_assistant.tools.sandbox import build_sandbox_registry


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
    """Build the service from config: Postgres if OPS_DATABASE_URL is set (else
    in-memory), and real Gmail/Calendar tools if a Google token is available (else
    the keyless sandbox)."""
    registry = None
    if settings.google_client_secrets:
        from ops_assistant.gworkspace.auth import load_credentials
        from ops_assistant.gworkspace.live import build_live_registry

        creds = load_credentials(settings.google_client_secrets, settings.google_token_path)
        if creds is not None:
            registry = build_live_registry(creds)
    if registry is None:
        registry = build_sandbox_registry()

    from ops_assistant.knowledge.base import KnowledgeBase
    from ops_assistant.knowledge.tools import build_knowledge_tool

    kb = KnowledgeBase.from_directory(settings.knowledge_dir)
    if kb.chunks:
        registry.register(build_knowledge_tool(kb))

    planner = None
    if settings.llm_api_key and settings.llm_model:
        from ops_assistant.planner.llm import LLMPlanner
        from ops_assistant.planner.openai_client import OpenAILLMClient

        client = OpenAILLMClient(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
        )
        planner = LLMPlanner(client, registry)

    kwargs: dict[str, Any] = {"registry": registry, "planner": planner}
    if settings.database_url:
        engine = build_engine(settings.database_url)
        create_schema(engine)
        return make_postgres_service(engine, **kwargs)
    return OpsService(**kwargs)
