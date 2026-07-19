"""Metrics derived from the append-only audit trail.

Because every meaningful event is already recorded, aggregate metrics are just a
fold over the audit log — no separate counters to drift out of sync with reality.

This is an O(n) fold over the events handed in, which is fine at the demo/portfolio
scale this targets. For a large trail the documented upgrade is to aggregate in the
store (a SQL ``GROUP BY event_type``) and hand the counts here, rather than
materialising every event on each ``/metrics`` request.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from ops_assistant.audit import AuditEvent, AuditEventType


def compute_metrics(events: Sequence[AuditEvent]) -> dict[str, object]:
    counts = Counter(event.event_type for event in events)
    succeeded = counts[AuditEventType.TOOL_SUCCEEDED]
    failed = counts[AuditEventType.TOOL_FAILED]
    tool_total = succeeded + failed
    return {
        "requests": counts[AuditEventType.REQUEST_CREATED],
        "workflows_completed": counts[AuditEventType.WORKFLOW_COMPLETED],
        "workflows_failed": counts[AuditEventType.WORKFLOW_FAILED],
        "tool_succeeded": succeeded,
        "tool_failed": failed,
        "tool_success_rate": round(succeeded / tool_total, 4) if tool_total else 1.0,
        "approvals_requested": counts[AuditEventType.APPROVAL_REQUESTED],
        "approvals_approved": counts[AuditEventType.APPROVAL_APPROVED],
        "approvals_rejected": counts[AuditEventType.APPROVAL_REJECTED],
        "risk_mismatches_detected": counts[AuditEventType.RISK_MISMATCH_DETECTED],
    }
