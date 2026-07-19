"""Live Telegram transport over the Bot API (httpx). A thin adapter around HTTP;
the bot's behaviour is tested against a fake, so this stays deliberately dumb."""

from __future__ import annotations

import httpx

from ops_assistant.telegram.bot import Button


class HttpTelegramTransport:
    def __init__(self, token: str, client: httpx.Client | None = None) -> None:
        self._base = f"https://api.telegram.org/bot{token}"
        self._client = client or httpx.Client(timeout=65.0)

    def send_message(
        self, chat_id: int, text: str, buttons: list[list[Button]] | None = None
    ) -> None:
        payload: dict[str, object] = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        keyboard = _keyboard(buttons)
        if keyboard is not None:
            payload["reply_markup"] = keyboard
        self._client.post(f"{self._base}/sendMessage", json=payload)

    def edit_message(
        self, chat_id: int, message_id: int, text: str, buttons: list[list[Button]] | None = None
    ) -> None:
        payload: dict[str, object] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "Markdown",
            "reply_markup": _keyboard(buttons) or {"inline_keyboard": []},
        }
        self._client.post(f"{self._base}/editMessageText", json=payload)

    def answer_callback(self, callback_id: str, text: str) -> None:
        self._client.post(
            f"{self._base}/answerCallbackQuery",
            json={"callback_query_id": callback_id, "text": text},
        )

    def get_updates(self, offset: int | None, timeout: int = 50) -> list[dict[str, object]]:
        response = self._client.post(
            f"{self._base}/getUpdates",
            json={"offset": offset, "timeout": timeout},
            timeout=timeout + 10,
        )
        data = response.json()
        result: list[dict[str, object]] = data.get("result", []) if data.get("ok") else []
        return result


def _keyboard(buttons: list[list[Button]] | None) -> dict[str, object] | None:
    if not buttons:
        return None
    return {
        "inline_keyboard": [
            [{"text": b.label, "callback_data": b.callback_data} for b in row] for row in buttons
        ]
    }
