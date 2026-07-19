"""HTTP surface: submit a request, inspect a workflow, approve/reject, read audit."""

from fastapi.testclient import TestClient

from ops_assistant.api.app import create_app
from ops_assistant.service import OpsService


def _client() -> TestClient:
    return TestClient(create_app(OpsService()))


def test_healthz() -> None:
    resp = _client().get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_submit_read_only_request_completes() -> None:
    client = _client()
    resp = client.post(
        "/requests", json={"text": "find free time", "user": "roman", "source": "web"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "completed"
    assert body["steps"][0]["status"] == "succeeded"


def test_submit_send_pauses_then_approve_executes() -> None:
    client = _client()
    submit = client.post(
        "/requests",
        json={"text": "send an email to anna@example.com", "user": "roman", "source": "web"},
    ).json()
    assert submit["status"] == "awaiting_approval"
    wid = submit["id"]
    approval_id = submit["pending_approvals"][0]["id"]

    approved = client.post(
        f"/workflows/{wid}/approvals/{approval_id}/approve", json={"actor": "roman"}
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "completed"


def test_reject_endpoint_rejects_workflow() -> None:
    client = _client()
    submit = client.post(
        "/requests",
        json={"text": "send an email to anna@example.com", "user": "roman", "source": "web"},
    ).json()
    wid, approval_id = submit["id"], submit["pending_approvals"][0]["id"]
    rejected = client.post(
        f"/workflows/{wid}/approvals/{approval_id}/reject", json={"actor": "roman", "reason": "no"}
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"


def test_get_workflow_and_unknown_is_404() -> None:
    client = _client()
    submit = client.post(
        "/requests", json={"text": "find free time", "user": "roman", "source": "web"}
    ).json()
    assert client.get(f"/workflows/{submit['id']}").status_code == 200
    assert client.get("/workflows/does-not-exist").status_code == 404


def test_audit_endpoint_lists_events() -> None:
    client = _client()
    submit = client.post(
        "/requests", json={"text": "find free time", "user": "roman", "source": "web"}
    ).json()
    audit = client.get(f"/workflows/{submit['id']}/audit")
    assert audit.status_code == 200
    types = [e["event_type"] for e in audit.json()]
    assert "request.created" in types
    assert "workflow.completed" in types


def test_double_approval_is_conflict() -> None:
    client = _client()
    submit = client.post(
        "/requests",
        json={"text": "send an email to anna@example.com", "user": "roman", "source": "web"},
    ).json()
    wid, approval_id = submit["id"], submit["pending_approvals"][0]["id"]
    client.post(f"/workflows/{wid}/approvals/{approval_id}/approve", json={"actor": "roman"})
    second = client.post(
        f"/workflows/{wid}/approvals/{approval_id}/approve", json={"actor": "roman"}
    )
    assert second.status_code == 409
    assert second.json()["code"] == "state_transition_error"
