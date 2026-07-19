"""Tool registry (server-side risk source of truth) and the keyless sandbox tools."""

import pytest

from ops_assistant.errors import UnknownToolError
from ops_assistant.models import RiskTier
from ops_assistant.tools.registry import ToolRegistry, ToolSpec
from ops_assistant.tools.sandbox import INJECTED_MESSAGE_ID, build_sandbox_registry


def _noop(args: object) -> str:
    return "ok"


def test_register_and_get() -> None:
    reg = ToolRegistry()
    spec = ToolSpec(name="x.do", risk=RiskTier.WRITE, description="d", handler=_noop)
    reg.register(spec)
    assert reg.get("x.do") is spec
    assert "x.do" in reg


def test_get_unknown_returns_none() -> None:
    assert ToolRegistry().get("nope") is None


def test_require_unknown_raises() -> None:
    with pytest.raises(UnknownToolError):
        ToolRegistry().require("nope")


def test_duplicate_registration_raises() -> None:
    reg = ToolRegistry()
    spec = ToolSpec(name="x.do", risk=RiskTier.WRITE, description="d", handler=_noop)
    reg.register(spec)
    with pytest.raises(ValueError):
        reg.register(spec)


# --- Sandbox: the canonical risk classification the whole thesis rests on. ---

EXPECTED_RISK = {
    "email.search": RiskTier.READ_ONLY,
    "email.get": RiskTier.READ_ONLY,
    "email.create_draft": RiskTier.DRAFT,
    "email.send": RiskTier.EXTERNAL_SIDE_EFFECT,
    "calendar.list_events": RiskTier.READ_ONLY,
    "calendar.find_free_time": RiskTier.READ_ONLY,
    "calendar.create_event": RiskTier.EXTERNAL_SIDE_EFFECT,
    "tasks.create": RiskTier.WRITE,
    "calendar.delete_event": RiskTier.DESTRUCTIVE,
}


@pytest.mark.parametrize(("name", "risk"), list(EXPECTED_RISK.items()))
def test_sandbox_tool_has_expected_risk_tier(name: str, risk: RiskTier) -> None:
    reg = build_sandbox_registry()
    spec = reg.require(name)
    assert spec.risk is risk


def test_sandbox_send_requires_recipient_arg() -> None:
    spec = build_sandbox_registry().require("email.send")
    assert "to" in spec.required_args


def test_sandbox_email_search_returns_messages() -> None:
    reg = build_sandbox_registry()
    result = reg.require("email.search").handler({"query": "newer_than:3d"})
    assert isinstance(result, list)
    assert result, "sandbox search should return at least one fake message"


def test_sandbox_create_event_echoes_schedule_and_attendees() -> None:
    # Parity with the live client: the schedule/invitees the human approves are
    # reflected in the result, not silently dropped.
    out = (
        build_sandbox_registry()
        .require("calendar.create_event")
        .handler({"title": "Sync", "start": "2026-07-20T15:00", "attendees": ["a@b.c"]})
    )
    assert out["start"] == "2026-07-20T15:00"
    assert out["attendees"] == ["a@b.c"]
    assert "end" not in out  # only echoes what was provided


def test_sandbox_handlers_return_shaped_data() -> None:
    reg = build_sandbox_registry()
    assert isinstance(reg.require("calendar.list_events").handler({}), list)
    assert isinstance(reg.require("calendar.find_free_time").handler({}), list)
    assert reg.require("calendar.create_event").handler({"title": "Sync"})["title"] == "Sync"
    assert reg.require("calendar.delete_event").handler({"id": "evt-1"})["deleted"] == "evt-1"
    assert reg.require("tasks.create").handler({"title": "Follow up"})["title"] == "Follow up"
    assert (
        reg.require("email.create_draft").handler({"to": "a@b.c"})["status"] == "created_not_sent"
    )
    assert reg.require("email.send").handler({"to": "a@b.c"})["status"] == "sent"


def test_sandbox_missing_required_argument_raises() -> None:
    from ops_assistant.errors import ArgumentError

    with pytest.raises(ArgumentError):
        build_sandbox_registry().require("email.send").handler({})  # no 'to'


def test_sandbox_email_get_unknown_id_raises() -> None:
    from ops_assistant.errors import ArgumentError

    with pytest.raises(ArgumentError):
        build_sandbox_registry().require("email.get").handler({"id": "nope"})


def test_sandbox_email_get_can_return_a_prompt_injection_body() -> None:
    # A hostile email whose body tries to hijack the assistant. Reading it is
    # read-only and safe; the guarantee is that its *content* can never become
    # an instruction. This fixture feeds the prompt-injection eval.
    reg = build_sandbox_registry()
    msg = reg.require("email.get").handler({"id": INJECTED_MESSAGE_ID})
    assert isinstance(msg, dict)
    assert "ignore" in str(msg["body"]).lower()
