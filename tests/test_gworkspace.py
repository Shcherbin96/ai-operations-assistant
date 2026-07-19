"""Google Workspace tool layer — real Gmail/Calendar tools over injectable clients.

The handlers are tested against fake clients; the live Google adapters (clients.py,
auth.py) are I/O and covered by running, not unit tests. The load-bearing
invariant: these tools register under the *same names and risk tiers* as the
sandbox, so swapping to real Google changes only the handlers.
"""

from dataclasses import dataclass, field
from typing import Any

import pytest

from ops_assistant.errors import ArgumentError
from ops_assistant.gworkspace.tools import build_google_registry
from ops_assistant.tools.sandbox import build_sandbox_registry


@dataclass
class FakeGmail:
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def search(self, query: str) -> list[dict[str, object]]:
        self.calls.append(("search", {"query": query}))
        return [{"id": "m1", "from": "anna@example.com", "subject": "Hi"}]

    def get(self, message_id: str) -> dict[str, object]:
        self.calls.append(("get", {"id": message_id}))
        return {"id": message_id, "body": "hello"}

    def create_draft(self, *, to: str, subject: str, body: str) -> dict[str, object]:
        self.calls.append(("create_draft", {"to": to, "subject": subject, "body": body}))
        return {"draft_id": "d1", "to": to}

    def send(self, *, to: str, subject: str, body: str) -> dict[str, object]:
        self.calls.append(("send", {"to": to, "subject": subject, "body": body}))
        return {"message_id": "s1", "to": to}


@dataclass
class FakeCalendar:
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    def list_events(
        self, *, time_min: str | None = None, time_max: str | None = None
    ) -> list[dict[str, object]]:
        self.calls.append(("list_events", {}))
        return [{"id": "e1", "title": "Standup"}]

    def find_free_time(self, *, duration_minutes: int = 30) -> list[dict[str, object]]:
        self.calls.append(("find_free_time", {"duration_minutes": duration_minutes}))
        return [{"start": "2026-07-20T11:00", "end": "2026-07-20T12:00"}]

    def create_event(
        self,
        *,
        title: str,
        start: str | None = None,
        end: str | None = None,
        attendees: list[str] | None = None,
    ) -> dict[str, object]:
        self.calls.append(("create_event", {"title": title}))
        return {"event_id": "e-new", "title": title}

    def delete_event(self, *, event_id: str) -> dict[str, object]:
        self.calls.append(("delete_event", {"event_id": event_id}))
        return {"deleted": event_id}


def _registry() -> tuple[Any, FakeGmail, FakeCalendar]:
    gmail, calendar = FakeGmail(), FakeCalendar()
    return build_google_registry(gmail, calendar), gmail, calendar


SHARED_TOOLS = [
    "email.search",
    "email.get",
    "email.create_draft",
    "email.send",
    "calendar.list_events",
    "calendar.find_free_time",
    "calendar.create_event",
    "calendar.delete_event",
]


@pytest.mark.parametrize("name", SHARED_TOOLS)
def test_google_tools_match_sandbox_risk_tiers(name: str) -> None:
    google = build_google_registry(FakeGmail(), FakeCalendar())
    sandbox = build_sandbox_registry()
    assert google.require(name).risk is sandbox.require(name).risk


def test_email_search_delegates_to_the_client() -> None:
    reg, gmail, _ = _registry()
    result = reg.require("email.search").handler({"query": "newer_than:3d"})
    assert gmail.calls == [("search", {"query": "newer_than:3d"})]
    assert isinstance(result, list) and result


def test_email_get_requires_id_and_delegates() -> None:
    reg, gmail, _ = _registry()
    assert reg.require("email.get").handler({"id": "m1"})["id"] == "m1"
    assert gmail.calls[-1] == ("get", {"id": "m1"})
    with pytest.raises(ArgumentError):
        reg.require("email.get").handler({})


def test_create_draft_and_send_pass_recipient() -> None:
    reg, gmail, _ = _registry()
    reg.require("email.create_draft").handler({"to": "a@b.c", "subject": "S", "body": "B"})
    reg.require("email.send").handler({"to": "a@b.c"})
    assert ("create_draft", {"to": "a@b.c", "subject": "S", "body": "B"}) in gmail.calls
    assert ("send", {"to": "a@b.c", "subject": "", "body": ""}) in gmail.calls


def test_send_requires_recipient() -> None:
    reg, _, _ = _registry()
    with pytest.raises(ArgumentError):
        reg.require("email.send").handler({})


def test_calendar_tools_delegate() -> None:
    reg, _, calendar = _registry()
    assert reg.require("calendar.list_events").handler({})
    assert reg.require("calendar.find_free_time").handler({"duration_minutes": 45})
    assert reg.require("calendar.create_event").handler({"title": "Sync"})["title"] == "Sync"
    kinds = [c[0] for c in calendar.calls]
    assert kinds == ["list_events", "find_free_time", "create_event"]


def test_create_event_requires_title() -> None:
    reg, _, _ = _registry()
    with pytest.raises(ArgumentError):
        reg.require("calendar.create_event").handler({})


def test_delete_event_delegates_and_requires_id() -> None:
    reg, _, calendar = _registry()
    assert reg.require("calendar.delete_event").handler({"id": "e1"})["deleted"] == "e1"
    assert calendar.calls[-1] == ("delete_event", {"event_id": "e1"})
    with pytest.raises(ArgumentError):
        reg.require("calendar.delete_event").handler({})
