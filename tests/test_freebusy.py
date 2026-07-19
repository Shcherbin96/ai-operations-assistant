"""Pure free/busy logic — the part of find_free_time that must be correct.

Extracted from the live Calendar client so it can be tested without the API,
after a review found that all-day events were being ignored.
"""

from datetime import UTC, datetime

from ops_assistant.gworkspace.freebusy import busy_intervals, free_slots


def _dt(hour: int, day: int = 20) -> datetime:
    return datetime(2026, 7, day, hour, 0, tzinfo=UTC)


def test_busy_intervals_parses_timed_events() -> None:
    items = [
        {
            "start": {"dateTime": "2026-07-20T09:00:00+00:00"},
            "end": {"dateTime": "2026-07-20T10:00:00+00:00"},
        }
    ]
    assert busy_intervals(items) == [(_dt(9), _dt(10))]


def test_busy_intervals_parses_all_day_events() -> None:
    # All-day events carry start.date / end.date (end exclusive), not dateTime.
    items = [{"start": {"date": "2026-07-20"}, "end": {"date": "2026-07-21"}}]
    assert busy_intervals(items) == [
        (datetime(2026, 7, 20, tzinfo=UTC), datetime(2026, 7, 21, tzinfo=UTC))
    ]


def test_busy_intervals_skips_malformed() -> None:
    assert busy_intervals([{"start": {}, "end": {}}, {}]) == []


def test_free_slots_no_busy_returns_the_whole_window() -> None:
    assert free_slots([], _dt(9), _dt(17), 30) == [
        {"start": _dt(9).isoformat(), "end": _dt(17).isoformat()}
    ]


def test_free_slots_finds_gaps_between_events() -> None:
    busy = [(_dt(9), _dt(10)), (_dt(12), _dt(13))]
    starts = [s["start"] for s in free_slots(busy, _dt(9), _dt(17), 60)]
    assert _dt(10).isoformat() in starts  # gap 10–12
    assert _dt(13).isoformat() in starts  # gap 13–17


def test_free_slots_respects_minimum_duration() -> None:
    busy = [(_dt(9), _dt(10))]
    # only a 30-min gap remains before the window closes; ask for 60 -> nothing
    assert free_slots(busy, _dt(9), datetime(2026, 7, 20, 10, 30, tzinfo=UTC), 60) == []


def test_free_slots_collapses_overlapping_events() -> None:
    busy = [(_dt(9), _dt(12)), (_dt(10), _dt(11))]
    slots = free_slots(busy, _dt(9), _dt(13), 30)
    assert slots == [{"start": _dt(12).isoformat(), "end": _dt(13).isoformat()}]


def test_all_day_event_blocks_the_whole_day() -> None:
    # The regression: an all-day event must remove that day from the free slots.
    items = [{"start": {"date": "2026-07-20"}, "end": {"date": "2026-07-21"}}]
    busy = busy_intervals(items)
    assert free_slots(busy, _dt(9), _dt(17), 30) == []
