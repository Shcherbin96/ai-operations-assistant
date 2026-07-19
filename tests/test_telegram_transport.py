"""HttpTelegramTransport against a mocked Bot API (httpx MockTransport).

Guards the review fix: messages are plain text (no parse_mode, so underscores in
tool/enum names can't 400) and HTTP failures surface instead of being swallowed.
"""

import json

import httpx
import pytest

from ops_assistant.telegram.bot import Button
from ops_assistant.telegram.transport import HttpTelegramTransport


def _capturing(
    requests: list[httpx.Request], response: httpx.Response | None = None
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return response or httpx.Response(200, json={"ok": True, "result": []})

    return httpx.Client(transport=httpx.MockTransport(handler))


def _body(request: httpx.Request) -> dict[str, object]:
    return json.loads(request.content)  # type: ignore[no-any-return]


def test_send_message_is_plain_text_without_markdown() -> None:
    reqs: list[httpx.Request] = []
    tx = HttpTelegramTransport("tok", _capturing(reqs))
    text = "calendar.find_free_time [read_only] — succeeded"  # odd underscore count
    tx.send_message(100, text)
    assert reqs[0].url.path.endswith("/sendMessage")
    body = _body(reqs[0])
    assert "parse_mode" not in body  # the fix: no Markdown, so this can't 400
    assert body["text"] == text
    assert "reply_markup" not in body


def test_send_message_renders_inline_keyboard() -> None:
    reqs: list[httpx.Request] = []
    tx = HttpTelegramTransport("tok", _capturing(reqs))
    tx.send_message(1, "hi", [[Button("Approve", "a:1"), Button("Reject", "r:1")]])
    keyboard = _body(reqs[0])["reply_markup"]["inline_keyboard"]  # type: ignore[index]
    assert keyboard == [
        [{"text": "Approve", "callback_data": "a:1"}, {"text": "Reject", "callback_data": "r:1"}]
    ]


def test_edit_message_clears_keyboard_when_none() -> None:
    reqs: list[httpx.Request] = []
    tx = HttpTelegramTransport("tok", _capturing(reqs))
    tx.edit_message(1, 2, "done", None)
    body = _body(reqs[0])
    assert body["reply_markup"] == {"inline_keyboard": []}
    assert "parse_mode" not in body


def test_answer_callback_posts_the_toast() -> None:
    reqs: list[httpx.Request] = []
    tx = HttpTelegramTransport("tok", _capturing(reqs))
    tx.answer_callback("cb1", "Approved")
    assert reqs[0].url.path.endswith("/answerCallbackQuery")
    assert _body(reqs[0]) == {"callback_query_id": "cb1", "text": "Approved"}


def test_send_message_raises_on_http_error_instead_of_swallowing() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"ok": False, "description": "can't parse entities"})

    tx = HttpTelegramTransport("tok", httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(httpx.HTTPStatusError):
        tx.send_message(1, "x")


def test_get_updates_parses_result_and_survives_not_ok() -> None:
    ok = httpx.Client(
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"ok": True, "result": [{"update_id": 5}]})
        )
    )
    assert HttpTelegramTransport("tok", ok).get_updates(None) == [{"update_id": 5}]

    not_ok = httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"ok": False}))
    )
    assert HttpTelegramTransport("tok", not_ok).get_updates(3) == []
