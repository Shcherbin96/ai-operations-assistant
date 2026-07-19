"""A tiny in-memory, per-user rate limiter for the public demo bot.

The open demo bot hands every request to a paid LLM, so one user firing a burst
could run up the bill. This caps each user to ``max_events`` per rolling
``window_seconds`` window. It is **first-line** defence only — the hard backstop
is a provider-side spend cap (see ``DEPLOY.md``); this just stops casual abuse
cheaply.

Per-process and in-memory, which is all a single-worker long-poller needs. Memory
stays bounded: once the map grows past a threshold, users whose window has fully
expired are swept out, so it tracks *recently active* users, not all-time ones.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable


class RateLimiter:
    """Sliding-window limiter: at most ``max_events`` per ``window_seconds``, per key."""

    _SWEEP_THRESHOLD = 1024

    def __init__(
        self,
        *,
        max_events: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if max_events < 1:
            raise ValueError("max_events must be >= 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        self._max = max_events
        self._window = window_seconds
        self._clock = clock
        self._hits: dict[int, deque[float]] = {}

    def allow(self, key: int) -> bool:
        """Record an event for ``key`` and report whether it is within budget."""
        now = self._clock()
        cutoff = now - self._window
        if len(self._hits) >= self._SWEEP_THRESHOLD:
            self._sweep(cutoff)
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= self._max:
            return False
        hits.append(now)
        return True

    def _sweep(self, cutoff: float) -> None:
        """Drop keys whose most recent event has aged out of the window."""
        stale = [k for k, hits in self._hits.items() if not hits or hits[-1] <= cutoff]
        for key in stale:
            del self._hits[key]
