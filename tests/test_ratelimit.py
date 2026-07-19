"""The per-user rate limiter, driven by a fake clock (no real time)."""

import pytest

from ops_assistant.telegram.ratelimit import RateLimiter


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _limiter(clock: FakeClock, *, max_events: int = 3, window: float = 60.0) -> RateLimiter:
    return RateLimiter(max_events=max_events, window_seconds=window, clock=clock)


def test_allows_up_to_the_limit_then_blocks() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, max_events=3)
    assert [limiter.allow(1) for _ in range(3)] == [True, True, True]
    assert limiter.allow(1) is False  # 4th within the same window


def test_window_expiry_re_allows() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, max_events=2, window=60.0)
    assert limiter.allow(1) is True
    assert limiter.allow(1) is True
    assert limiter.allow(1) is False
    clock.now += 61.0  # the whole window passes
    assert limiter.allow(1) is True


def test_partial_window_slides() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, max_events=2, window=60.0)
    limiter.allow(1)  # t=1000
    clock.now += 30.0
    limiter.allow(1)  # t=1030 -> now 2 in window
    assert limiter.allow(1) is False  # still 2 in [1000..1060]
    clock.now += 31.0  # t=1091: the first (1000) has aged out, 1030 remains
    assert limiter.allow(1) is True


def test_keys_are_isolated() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, max_events=1)
    assert limiter.allow(1) is True
    assert limiter.allow(2) is True  # a different user is unaffected
    assert limiter.allow(1) is False


def test_invalid_configuration_is_rejected() -> None:
    clock = FakeClock()
    with pytest.raises(ValueError):
        RateLimiter(max_events=0, window_seconds=60.0, clock=clock)
    with pytest.raises(ValueError):
        RateLimiter(max_events=1, window_seconds=0, clock=clock)


def test_stale_keys_are_swept_when_the_map_grows() -> None:
    clock = FakeClock()
    limiter = _limiter(clock, max_events=1, window=60.0)
    # Fill past the sweep threshold with one-off users at t=1000.
    for user in range(RateLimiter._SWEEP_THRESHOLD):
        limiter.allow(user)
    assert len(limiter._hits) == RateLimiter._SWEEP_THRESHOLD
    # Long after their window, a new user triggers a sweep that clears the expired ones.
    clock.now += 120.0
    limiter.allow(10_000)
    assert len(limiter._hits) == 1  # only the fresh user survives
