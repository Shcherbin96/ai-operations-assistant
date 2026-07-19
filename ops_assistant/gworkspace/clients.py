"""Thin client interfaces for Gmail and Calendar.

The tool handlers depend on these small protocols, not on the Google SDK, so they
are testable with fakes. The live adapters (``GoogleGmailClient`` /
``GoogleCalendarClient``) live in ``live.py`` and are the only code that touches
``googleapiclient``.
"""

from __future__ import annotations

from typing import Protocol


class GmailClient(Protocol):
    def search(self, query: str) -> list[dict[str, object]]: ...

    def get(self, message_id: str) -> dict[str, object]: ...

    def create_draft(self, *, to: str, subject: str, body: str) -> dict[str, object]: ...

    def send(self, *, to: str, subject: str, body: str) -> dict[str, object]: ...


class CalendarClient(Protocol):
    def list_events(
        self, *, time_min: str | None = None, time_max: str | None = None
    ) -> list[dict[str, object]]: ...

    def find_free_time(self, *, duration_minutes: int = 30) -> list[dict[str, object]]: ...

    def create_event(
        self,
        *,
        title: str,
        start: str | None = None,
        end: str | None = None,
        attendees: list[str] | None = None,
    ) -> dict[str, object]: ...

    def delete_event(self, *, event_id: str) -> dict[str, object]: ...
