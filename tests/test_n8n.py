"""n8n execution layer: signed webhooks + a server-side workflow allowlist."""

from dataclasses import dataclass, field

import pytest

from ops_assistant.errors import ArgumentError
from ops_assistant.models import RiskTier
from ops_assistant.n8n.client import sign
from ops_assistant.n8n.tools import build_n8n_tool


@dataclass
class FakeN8n:
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)

    def trigger(self, workflow: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((workflow, payload))
        return {"workflow": workflow, "status": "triggered"}


def test_sign_is_deterministic_hmac() -> None:
    a = sign(b"hello", "secret")
    assert a == sign(b"hello", "secret")
    assert a != sign(b"hello", "other")
    assert len(a) == 64  # sha256 hex


def test_n8n_tool_is_gated_and_allowlisted() -> None:
    spec = build_n8n_tool(FakeN8n(), ["update_crm"])
    assert spec.name == "n8n.run"
    assert spec.risk is RiskTier.EXTERNAL_SIDE_EFFECT  # always requires approval
    assert spec.required_args == ("workflow",)


def test_allowlisted_workflow_is_triggered() -> None:
    fake = FakeN8n()
    spec = build_n8n_tool(fake, ["update_crm"])
    result = spec.handler({"workflow": "update_crm", "payload": {"id": 7}})
    assert result["status"] == "triggered"
    assert fake.calls == [("update_crm", {"id": 7})]


def test_non_allowlisted_workflow_is_refused() -> None:
    spec = build_n8n_tool(FakeN8n(), ["update_crm"])
    with pytest.raises(ArgumentError):
        spec.handler({"workflow": "delete_everything"})


def test_missing_workflow_arg_is_refused() -> None:
    spec = build_n8n_tool(FakeN8n(), ["update_crm"])
    with pytest.raises(ArgumentError):
        spec.handler({})


def test_non_dict_payload_becomes_empty() -> None:
    fake = FakeN8n()
    spec = build_n8n_tool(fake, ["update_crm"])
    spec.handler({"workflow": "update_crm", "payload": "not a dict"})
    assert fake.calls == [("update_crm", {})]
