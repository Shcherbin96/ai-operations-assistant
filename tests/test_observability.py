"""Metrics computed from the append-only audit trail (the source of truth)."""

from collections.abc import Callable
from datetime import UTC, datetime

from ops_assistant.observability import compute_metrics
from ops_assistant.service import OpsService


def _counter_ids() -> Callable[[], str]:
    n = iter(range(1, 100000))
    return lambda: f"id-{next(n)}"


def _svc() -> OpsService:
    return OpsService(
        clock=lambda: datetime(2026, 7, 19, 12, 0, tzinfo=UTC), id_factory=_counter_ids()
    )


def test_metrics_over_a_read_only_and_a_gated_workflow() -> None:
    svc = _svc()
    svc.submit(text="find free time", user="u", source="t")  # read-only -> completed
    view = svc.submit(text="send an email to a@b.c", user="u", source="t")  # gated
    svc.approve(view.id, view.pending_approvals[0].id, actor="u")  # -> completed

    m = compute_metrics(svc.all_audit())
    assert m["requests"] == 2
    assert m["workflows_completed"] == 2
    assert m["approvals_requested"] == 1
    assert m["approvals_approved"] == 1
    assert m["tool_succeeded"] >= 2
    assert m["tool_success_rate"] == 1.0


def test_metrics_on_empty_audit_are_safe() -> None:
    m = compute_metrics(_svc().all_audit())
    assert m["requests"] == 0
    assert m["tool_success_rate"] == 1.0  # no calls -> vacuously perfect


def test_rejection_and_mismatch_are_counted() -> None:
    from ops_assistant.models import OperationRequest, Plan, PlanStep, RiskTier

    class _Injector:
        def plan(self, request: OperationRequest) -> Plan:
            return Plan(
                summary="s",
                steps=[
                    PlanStep(
                        id="s1",
                        tool="email.send",
                        arguments={"to": "a@b.c"},
                        claimed_risk=RiskTier.READ_ONLY,
                    )
                ],
            )

    svc = OpsService(
        planner=_Injector(),
        clock=lambda: datetime(2026, 7, 19, 12, 0, tzinfo=UTC),
        id_factory=_counter_ids(),
    )
    view = svc.submit(text="x", user="u", source="t")
    svc.reject(view.id, view.pending_approvals[0].id, actor="u")
    m = compute_metrics(svc.all_audit())
    assert m["approvals_rejected"] == 1
    assert m["risk_mismatches_detected"] == 1
