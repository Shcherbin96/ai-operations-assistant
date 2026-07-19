"""Pure free/busy computation for Calendar's find_free_time.

Kept separate from the live API client so the tricky part — turning events into
busy intervals (including all-day events) and sweeping for gaps — is unit-tested.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta


def _parse(node: object) -> datetime | None:
    if not isinstance(node, dict):
        return None
    timed = node.get("dateTime")
    if isinstance(timed, str):
        return datetime.fromisoformat(timed)
    # All-day events use a bare date; treat it as a UTC full-day interval.
    all_day = node.get("date")
    if isinstance(all_day, str):
        return datetime.fromisoformat(all_day).replace(tzinfo=UTC)
    return None


def busy_intervals(items: list[dict[str, object]]) -> list[tuple[datetime, datetime]]:
    """Turn Calendar events into (start, end) busy intervals, all-day included."""
    intervals: list[tuple[datetime, datetime]] = []
    for event in items:
        start = _parse(event.get("start"))
        end = _parse(event.get("end"))
        if start is not None and end is not None:
            intervals.append((start, end))
    return intervals


def free_slots(
    busy: list[tuple[datetime, datetime]],
    window_start: datetime,
    window_end: datetime,
    duration_minutes: int,
) -> list[dict[str, object]]:
    """Sweep the busy intervals and return the gaps at least ``duration`` long."""
    minimum = timedelta(minutes=duration_minutes)
    slots: list[dict[str, object]] = []
    cursor = window_start
    for start, end in sorted(busy):
        if start > cursor and (start - cursor) >= minimum:
            slots.append({"start": cursor.isoformat(), "end": start.isoformat()})
        cursor = max(cursor, end)
    if (window_end - cursor) >= minimum:
        slots.append({"start": cursor.isoformat(), "end": window_end.isoformat()})
    return slots
