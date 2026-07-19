"""Live Gmail/Calendar clients over google-api-python-client.

The only code that touches the Google SDK. Deliberately thin: each method maps one
tool call to one (or few) API calls and returns plain dicts matching the client
protocols. Untested in CI (real API I/O) — verified by running the auth flow and a
real request.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any

from ops_assistant.gworkspace.tools import build_google_registry
from ops_assistant.tools.registry import ToolRegistry


def _mime(to: str, subject: str, body: str) -> str:
    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    return base64.urlsafe_b64encode(message.as_bytes()).decode()


def _plain_text(payload: dict[str, Any]) -> str:
    if payload.get("mimeType") == "text/plain":
        data = payload.get("body", {}).get("data")
        if data:
            return base64.urlsafe_b64decode(data).decode(errors="replace")
    for part in payload.get("parts", []) or []:
        text = _plain_text(part)
        if text:
            return text
    return ""


class GoogleGmailClient:
    def __init__(self, service: Any) -> None:
        self._s = service

    def search(self, query: str) -> list[dict[str, object]]:
        resp = self._s.users().messages().list(userId="me", q=query, maxResults=10).execute()
        out: list[dict[str, object]] = []
        for meta in resp.get("messages", []):
            full = (
                self._s.users()
                .messages()
                .get(
                    userId="me",
                    id=meta["id"],
                    format="metadata",
                    metadataHeaders=["From", "Subject"],
                )
                .execute()
            )
            headers = {h["name"]: h["value"] for h in full.get("payload", {}).get("headers", [])}
            out.append(
                {
                    "id": meta["id"],
                    "from": headers.get("From", ""),
                    "subject": headers.get("Subject", ""),
                }
            )
        return out

    def get(self, message_id: str) -> dict[str, object]:
        full = self._s.users().messages().get(userId="me", id=message_id, format="full").execute()
        payload = full.get("payload", {})
        headers = {h["name"]: h["value"] for h in payload.get("headers", [])}
        return {
            "id": message_id,
            "from": headers.get("From", ""),
            "subject": headers.get("Subject", ""),
            "body": _plain_text(payload),
        }

    def create_draft(self, *, to: str, subject: str, body: str) -> dict[str, object]:
        draft = (
            self._s.users()
            .drafts()
            .create(userId="me", body={"message": {"raw": _mime(to, subject, body)}})
            .execute()
        )
        return {"draft_id": draft["id"], "to": to, "status": "created_not_sent"}

    def send(self, *, to: str, subject: str, body: str) -> dict[str, object]:
        sent = (
            self._s.users()
            .messages()
            .send(userId="me", body={"raw": _mime(to, subject, body)})
            .execute()
        )
        return {"message_id": sent["id"], "to": to, "status": "sent"}


class GoogleCalendarClient:
    def __init__(self, service: Any) -> None:
        self._s = service

    def list_events(
        self, *, time_min: str | None = None, time_max: str | None = None
    ) -> list[dict[str, object]]:
        resp = (
            self._s.events()
            .list(
                calendarId="primary",
                timeMin=time_min or datetime.now(UTC).isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=10,
            )
            .execute()
        )
        return [
            {
                "id": e["id"],
                "title": e.get("summary", ""),
                "start": e["start"].get("dateTime", e["start"].get("date")),
                "end": e["end"].get("dateTime", e["end"].get("date")),
            }
            for e in resp.get("items", [])
        ]

    def find_free_time(self, *, duration_minutes: int = 30) -> list[dict[str, object]]:
        now = datetime.now(UTC)
        horizon = now + timedelta(days=7)
        resp = (
            self._s.events()
            .list(
                calendarId="primary",
                timeMin=now.isoformat(),
                timeMax=horizon.isoformat(),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )
        busy: list[tuple[datetime, datetime]] = []
        for e in resp.get("items", []):
            start, end = e["start"].get("dateTime"), e["end"].get("dateTime")
            if start and end:
                busy.append((datetime.fromisoformat(start), datetime.fromisoformat(end)))

        slots: list[dict[str, object]] = []
        cursor = now
        for start, end in busy:
            if (start - cursor) >= timedelta(minutes=duration_minutes):
                slots.append({"start": cursor.isoformat(), "end": start.isoformat()})
            cursor = max(cursor, end)
        if (horizon - cursor) >= timedelta(minutes=duration_minutes):
            slots.append({"start": cursor.isoformat(), "end": horizon.isoformat()})
        return slots[:5]

    def create_event(
        self,
        *,
        title: str,
        start: str | None = None,
        end: str | None = None,
        attendees: list[str] | None = None,
    ) -> dict[str, object]:
        begin = start or datetime.now(UTC).isoformat()
        finish = end or (datetime.now(UTC) + timedelta(minutes=30)).isoformat()
        body: dict[str, Any] = {
            "summary": title,
            "start": {"dateTime": begin},
            "end": {"dateTime": finish},
        }
        if attendees:
            body["attendees"] = [{"email": a} for a in attendees]
        event = self._s.events().insert(calendarId="primary", body=body).execute()
        return {"event_id": event["id"], "title": title, "status": "created"}


def build_live_registry(credentials: Any) -> ToolRegistry:
    """Build a tool registry backed by the real Gmail/Calendar APIs."""
    from googleapiclient.discovery import build

    gmail = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    calendar = build("calendar", "v3", credentials=credentials, cache_discovery=False)
    return build_google_registry(GoogleGmailClient(gmail), GoogleCalendarClient(calendar))
