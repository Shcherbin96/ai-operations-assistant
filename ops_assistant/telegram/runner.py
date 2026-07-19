"""Long-polling runner. ``dispatch_update`` (pure, tested) routes one Telegram
update to the bot; ``run_polling`` is the live getUpdates loop."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

from ops_assistant.telegram.bot import TelegramBot
from ops_assistant.telegram.transport import HttpTelegramTransport


def _display_name(user: Mapping[str, Any]) -> str:
    return str(user.get("username") or user.get("first_name") or user.get("id", "unknown"))


def dispatch_update(bot: TelegramBot, update: Mapping[str, Any]) -> None:
    """Route one update to the bot, tolerating malformed or non-text updates."""
    if "message" in update:
        message = update["message"]
        text = message.get("text")
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        if text is None or "id" not in chat or "id" not in sender:
            return
        bot.handle_message(
            chat_id=chat["id"], user_id=sender["id"], user_name=_display_name(sender), text=text
        )
    elif "callback_query" in update:
        query = update["callback_query"]
        message = query.get("message") or {}
        chat = message.get("chat") or {}
        sender = query.get("from") or {}
        if "id" not in chat or "message_id" not in message or "id" not in sender:
            return
        bot.handle_callback(
            callback_id=query.get("id", ""),
            chat_id=chat["id"],
            message_id=message["message_id"],
            user_id=sender["id"],
            user_name=_display_name(sender),
            data=query.get("data", ""),
        )


def run_polling(  # pragma: no cover - live I/O loop
    bot: TelegramBot,
    transport: HttpTelegramTransport,
    *,
    should_stop: Callable[[], bool] | None = None,
) -> None:
    offset: int | None = None
    while should_stop is None or not should_stop():
        try:
            for update in transport.get_updates(offset):
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    offset = update_id + 1
                dispatch_update(bot, update)
        except Exception as exc:  # keep the bot alive across transient failures
            print(f"telegram poll error: {type(exc).__name__}: {exc}")
            time.sleep(3)
