"""Real Gmail/Calendar tools, registered under the same names and risk tiers as
the sandbox. Only the handlers differ — they delegate to the injected clients."""

from __future__ import annotations

from collections.abc import Mapping

from ops_assistant.errors import ArgumentError
from ops_assistant.gworkspace.clients import CalendarClient, GmailClient
from ops_assistant.models import RiskTier
from ops_assistant.tools.registry import ToolRegistry, ToolSpec


def _require(args: Mapping[str, object], key: str) -> object:
    if key not in args:
        raise ArgumentError(f"missing required argument: {key}")
    return args[key]


def build_google_registry(gmail: GmailClient, calendar: CalendarClient) -> ToolRegistry:
    def email_search(args: Mapping[str, object]) -> object:
        return gmail.search(str(args.get("query", "")))

    def email_get(args: Mapping[str, object]) -> object:
        return gmail.get(str(_require(args, "id")))

    def email_create_draft(args: Mapping[str, object]) -> object:
        return gmail.create_draft(
            to=str(_require(args, "to")),
            subject=str(args.get("subject", "")),
            body=str(args.get("body", "")),
        )

    def email_send(args: Mapping[str, object]) -> object:
        return gmail.send(
            to=str(_require(args, "to")),
            subject=str(args.get("subject", "")),
            body=str(args.get("body", "")),
        )

    def calendar_list_events(args: Mapping[str, object]) -> object:
        return calendar.list_events()

    def calendar_find_free_time(args: Mapping[str, object]) -> object:
        raw = args.get("duration_minutes", 30)
        minutes = int(raw) if isinstance(raw, int | str) else 30
        return calendar.find_free_time(duration_minutes=minutes)

    def calendar_create_event(args: Mapping[str, object]) -> object:
        return calendar.create_event(title=str(_require(args, "title")))

    specs = (
        ToolSpec("email.search", RiskTier.READ_ONLY, "Search the mailbox", email_search),
        ToolSpec("email.get", RiskTier.READ_ONLY, "Read one message", email_get, ("id",)),
        ToolSpec(
            "email.create_draft",
            RiskTier.DRAFT,
            "Create an unsent draft",
            email_create_draft,
            ("to",),
        ),
        ToolSpec("email.send", RiskTier.EXTERNAL_SIDE_EFFECT, "Send an email", email_send, ("to",)),
        ToolSpec("calendar.list_events", RiskTier.READ_ONLY, "List events", calendar_list_events),
        ToolSpec(
            "calendar.find_free_time",
            RiskTier.READ_ONLY,
            "Find free slots",
            calendar_find_free_time,
        ),
        ToolSpec(
            "calendar.create_event",
            RiskTier.EXTERNAL_SIDE_EFFECT,
            "Create an event (invites attendees)",
            calendar_create_event,
            ("title",),
        ),
    )

    registry = ToolRegistry()
    for spec in specs:
        registry.register(spec)
    return registry
