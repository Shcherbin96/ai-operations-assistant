"""Integration tests against a real Postgres (spun up with testcontainers).

These prove the Stage-2 guarantees that only a database can give: state survives a
fresh service instance, the audit trigger makes history immutable, optimistic
locking rejects a stale write, and idempotency holds at the row level.

Run with a running Docker daemon:  uv run pytest -m integration
"""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime

import pytest

pytest.importorskip("testcontainers")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402
from sqlalchemy.exc import DBAPIError  # noqa: E402
from testcontainers.postgres import PostgresContainer  # noqa: E402

from ops_assistant.errors import ConflictError  # noqa: E402
from ops_assistant.factory import build_engine, make_postgres_service  # noqa: E402
from ops_assistant.gateway import ToolResult  # noqa: E402
from ops_assistant.models import StepStatus, WorkflowStatus  # noqa: E402
from ops_assistant.persistence import schema  # noqa: E402
from ops_assistant.persistence.postgres import (  # noqa: E402
    PostgresIdempotencyStore,
    PostgresWorkflowStore,
)

pytestmark = pytest.mark.integration

FIXED_NOW = datetime(2026, 7, 19, 12, 0, tzinfo=UTC)


def _clock() -> datetime:
    return FIXED_NOW


def _counter_ids() -> Callable[[], str]:
    n = iter(range(1, 100000))
    return lambda: f"id-{next(n)}"


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    try:
        container = PostgresContainer("postgres:16-alpine", driver="psycopg")
        container.start()
    except Exception as exc:  # noqa: BLE001 - Docker not available -> skip, don't fail
        pytest.skip(f"Docker/Postgres unavailable: {exc}")
    try:
        eng = build_engine(container.get_connection_url())
        schema.create_schema(eng)
        yield eng
        eng.dispose()
    finally:
        container.stop()


@pytest.fixture(autouse=True)
def _clean(engine: Engine) -> None:
    # audit_events is append-only (incl. a BEFORE TRUNCATE trigger), so the test
    # reset must disable that trigger to wipe the table between cases.
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE audit_events DISABLE TRIGGER audit_events_no_truncate"))
        conn.execute(
            text(
                "TRUNCATE workflows, steps, approvals, audit_events, tool_executions "
                "RESTART IDENTITY CASCADE"
            )
        )
        conn.execute(text("ALTER TABLE audit_events ENABLE TRIGGER audit_events_no_truncate"))


def test_awaiting_approval_workflow_survives_a_fresh_service_instance(engine: Engine) -> None:
    svc_a = make_postgres_service(engine, clock=_clock, id_factory=_counter_ids())
    view = svc_a.submit(text="send an email to anna@example.com", user="roman", source="pg")
    assert view.status is WorkflowStatus.AWAITING_APPROVAL
    wid = view.id

    # A brand-new service instance (as if the process restarted) resumes it.
    svc_b = make_postgres_service(engine, clock=_clock, id_factory=_counter_ids())
    reloaded = svc_b.get(wid)
    assert reloaded.status is WorkflowStatus.AWAITING_APPROVAL
    assert len(reloaded.pending_approvals) == 1

    done = svc_b.approve(wid, reloaded.pending_approvals[0].id, actor="roman")
    assert done.status is WorkflowStatus.COMPLETED
    assert done.steps[0].status is StepStatus.SUCCEEDED


def test_read_only_workflow_and_audit_persist(engine: Engine) -> None:
    svc = make_postgres_service(engine, clock=_clock, id_factory=_counter_ids())
    view = svc.submit(text="find free time", user="roman", source="pg")
    assert view.status is WorkflowStatus.COMPLETED

    fresh = make_postgres_service(engine, clock=_clock, id_factory=_counter_ids())
    events = [e.event_type.value for e in fresh.audit_for(view.id)]
    assert "request.created" in events
    assert "workflow.completed" in events


def test_clarification_workflow_persists(engine: Engine) -> None:
    # Regression for the fixed bug: the clarification early-return must persist,
    # so a reload (or a different worker) sees the same thing the POST returned.
    svc = make_postgres_service(engine, clock=_clock, id_factory=_counter_ids())
    view = svc.submit(text="send an update", user="roman", source="pg")  # no recipient
    assert view.requires_clarification is True

    reloaded = make_postgres_service(engine, clock=_clock, id_factory=_counter_ids()).get(view.id)
    assert reloaded.requires_clarification is True
    assert reloaded.clarification_question


def test_rejected_workflow_persists(engine: Engine) -> None:
    svc = make_postgres_service(engine, clock=_clock, id_factory=_counter_ids())
    view = svc.submit(text="send an email to anna@example.com", user="roman", source="pg")
    svc.reject(view.id, view.pending_approvals[0].id, actor="roman", reason="no")

    reloaded = make_postgres_service(engine, clock=_clock, id_factory=_counter_ids()).get(view.id)
    assert reloaded.status is WorkflowStatus.REJECTED
    assert reloaded.steps[0].status is StepStatus.REJECTED


def test_multi_step_dependent_workflow_round_trips(engine: Engine) -> None:
    svc = make_postgres_service(engine, clock=_clock, id_factory=_counter_ids())
    view = svc.submit(text="draft replies to recent emails", user="roman", source="pg")
    assert view.status is WorkflowStatus.COMPLETED

    reloaded = make_postgres_service(engine, clock=_clock, id_factory=_counter_ids()).get(view.id)
    tools = [s.tool for s in reloaded.steps]
    assert tools == ["email.search", "email.create_draft"]  # ordinal preserved
    draft = reloaded.steps[1]
    assert "s1" in draft.depends_on  # depends_on serialized
    assert all(s.status is StepStatus.SUCCEEDED for s in reloaded.steps)


def test_truncate_of_audit_is_blocked(engine: Engine) -> None:
    svc = make_postgres_service(engine, clock=_clock, id_factory=_counter_ids())
    svc.submit(text="find free time", user="roman", source="pg")
    with pytest.raises(DBAPIError), engine.begin() as conn:
        conn.execute(text("TRUNCATE audit_events"))


def test_audit_trigger_blocks_update_and_delete(engine: Engine) -> None:
    svc = make_postgres_service(engine, clock=_clock, id_factory=_counter_ids())
    view = svc.submit(text="find free time", user="roman", source="pg")
    assert svc.audit_for(view.id)  # some events exist

    with pytest.raises(DBAPIError), engine.begin() as conn:
        conn.execute(text("UPDATE audit_events SET actor = 'tamperer'"))

    with pytest.raises(DBAPIError), engine.begin() as conn:
        conn.execute(text("DELETE FROM audit_events"))

    # history is intact
    assert svc.audit_for(view.id)


def test_concurrent_approve_decides_exactly_once(engine: Engine) -> None:
    # Two app instances (independent RLocks) approving the same approval against one
    # Postgres: the compare-and-set makes the decision atomic, so only one wins.
    from concurrent.futures import ThreadPoolExecutor

    svc_a = make_postgres_service(engine, clock=_clock, id_factory=_counter_ids())
    view = svc_a.submit(text="send an email to anna@example.com", user="roman", source="pg")
    wid, approval_id = view.id, view.pending_approvals[0].id
    svc_b = make_postgres_service(engine, clock=_clock, id_factory=_counter_ids())

    def worker(svc: object) -> str:
        try:
            svc.approve(wid, approval_id, actor="roman")  # type: ignore[attr-defined]
            return "ok"
        except Exception as exc:  # noqa: BLE001 - asserting on outcome, not type
            return f"err:{type(exc).__name__}"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = [f.result() for f in [pool.submit(worker, svc_a), pool.submit(worker, svc_b)]]

    assert outcomes.count("ok") == 1  # exactly one succeeded
    assert sum(o.startswith("err") for o in outcomes) == 1  # the other was refused


def test_optimistic_lock_rejects_a_stale_write(engine: Engine) -> None:
    svc = make_postgres_service(engine, clock=_clock, id_factory=_counter_ids())
    view = svc.submit(text="find free time", user="roman", source="pg")

    store = PostgresWorkflowStore(engine)
    first = store.get(view.id)
    second = store.get(view.id)
    assert first is not None and second is not None
    assert first.version == second.version

    store.save(first)  # bumps the DB version
    with pytest.raises(ConflictError):
        store.save(second)  # stale expected version -> conflict


def test_idempotent_execution_records_once(engine: Engine) -> None:
    store = PostgresIdempotencyStore(engine, _clock)
    key = "wf-1:step-1"
    store.put(
        key, workflow_id="wf-1", step_id="step-1", result=ToolResult("email.send", {"id": 1}, False)
    )

    replay = store.get(key)
    assert replay is not None
    assert replay.replayed is True
    assert replay.output == {"id": 1}

    # A second put with the same key is a no-op (ON CONFLICT DO NOTHING).
    store.put(
        key,
        workflow_id="wf-1",
        step_id="step-1",
        result=ToolResult("email.send", {"id": 999}, False),
    )
    again = store.get(key)
    assert again is not None
    assert again.output == {"id": 1}  # original preserved
