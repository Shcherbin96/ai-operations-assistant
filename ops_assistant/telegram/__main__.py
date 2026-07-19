"""``python -m ops_assistant.telegram`` runs the Telegram bot via long-polling.

Reads the token (and optional user allowlist / database URL) from the environment.
"""

from __future__ import annotations

import signal
from types import FrameType

from ops_assistant.config import get_settings
from ops_assistant.factory import service_from_settings
from ops_assistant.telegram.bot import TelegramBot
from ops_assistant.telegram.ratelimit import RateLimiter
from ops_assistant.telegram.runner import run_polling
from ops_assistant.telegram.transport import HttpTelegramTransport


def _parse_allowed(raw: str) -> frozenset[int]:  # pragma: no cover
    return frozenset(int(part) for part in raw.split(",") if part.strip())


def main() -> None:  # pragma: no cover - live entrypoint
    settings = get_settings()
    if not settings.telegram_token:
        raise SystemExit("Set OPS_TELEGRAM_TOKEN (from @BotFather) to run the Telegram bot.")
    service = service_from_settings(settings)
    allowed = _parse_allowed(settings.telegram_allowed_users)
    limiter = (
        RateLimiter(
            max_events=settings.telegram_rate_limit,
            window_seconds=settings.telegram_rate_window_seconds,
        )
        if settings.telegram_rate_limit > 0
        else None
    )
    transport = HttpTelegramTransport(settings.telegram_token)
    bot = TelegramBot(service, transport, allowed or None, limiter)

    # Graceful shutdown: a Fly redeploy sends SIGTERM. Stop after the current poll
    # instead of being SIGKILLed mid-request, so no in-flight work is torn open.
    stopping = False

    def _request_stop(signum: int, _frame: FrameType | None) -> None:
        nonlocal stopping
        stopping = True
        print(f"received {signal.Signals(signum).name}; finishing the current poll then exiting…")

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    print("AI Operations Assistant bot is polling. Press Ctrl+C to stop.")
    run_polling(bot, transport, should_stop=lambda: stopping)


if __name__ == "__main__":  # pragma: no cover
    main()
