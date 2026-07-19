"""Telegram bot logic, driven through a fake transport (no network, no token)."""

from dataclasses import dataclass, field

from ops_assistant.service import OpsService
from ops_assistant.telegram.bot import Button, TelegramBot


@dataclass
class _Sent:
    chat_id: int
    text: str
    buttons: list[list[Button]] | None


@dataclass
class _Edit:
    chat_id: int
    message_id: int
    text: str
    buttons: list[list[Button]] | None


@dataclass
class FakeTransport:
    sent: list[_Sent] = field(default_factory=list)
    edits: list[_Edit] = field(default_factory=list)
    answered: list[tuple[str, str]] = field(default_factory=list)

    def send_message(
        self, chat_id: int, text: str, buttons: list[list[Button]] | None = None
    ) -> None:
        self.sent.append(_Sent(chat_id, text, buttons))

    def edit_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        buttons: list[list[Button]] | None = None,
    ) -> None:
        self.edits.append(_Edit(chat_id, message_id, text, buttons))

    def answer_callback(self, callback_id: str, text: str) -> None:
        self.answered.append((callback_id, text))


def _bot(**kw: object) -> tuple[TelegramBot, FakeTransport]:
    tx = FakeTransport()
    return TelegramBot(OpsService(), tx, **kw), tx  # type: ignore[arg-type]


def _msg(bot: TelegramBot, text: str, user_id: int = 1) -> None:
    bot.handle_message(chat_id=100, user_id=user_id, user_name="roman", text=text)


def test_start_sends_a_welcome() -> None:
    bot, tx = _bot()
    _msg(bot, "/start")
    assert len(tx.sent) == 1
    assert tx.sent[0].buttons is None
    assert "assistant" in tx.sent[0].text.lower()


def test_read_only_request_replies_completed_without_buttons() -> None:
    bot, tx = _bot()
    _msg(bot, "find free time")
    assert len(tx.sent) == 1
    assert "completed" in tx.sent[0].text.lower()
    assert tx.sent[0].buttons is None


def test_read_only_result_is_shown_not_just_status() -> None:
    # The point of asking: the reply must contain the actual answer (the free
    # slots), not merely "succeeded".
    bot, tx = _bot()
    _msg(bot, "find free time")
    text = tx.sent[0].text
    assert "2026-07-20" in text  # a real free-slot time from the sandbox


def test_email_search_result_lists_the_messages() -> None:
    bot, tx = _bot()
    _msg(bot, "draft replies to recent emails")
    text = tx.sent[0].text
    assert "anna@example.com" in text  # a found sender is shown, not hidden


def test_format_output_renders_every_shape() -> None:
    from ops_assistant.telegram.bot import _format_output

    assert "(nothing found)" in _format_output([])
    assert "a@b.c — Hi" in _format_output([{"from": "a@b.c", "subject": "Hi"}])
    assert "Standup (2026-07-20)" in _format_output([{"title": "Standup", "start": "2026-07-20"}])
    assert "09:00 → 10:00" in _format_output([{"start": "09:00", "end": "10:00"}])
    assert "and 3 more" in _format_output([{"x": i} for i in range(8)])
    assert "draft_id: d1" in _format_output({"draft_id": "d1", "to": "a@b.c"})
    cited = _format_output(
        [{"source": "returns.md", "text": "Returns within 30 days", "section": "R"}]
    )
    assert "returns.md — Returns within 30 days" in cited
    assert _format_output("done") == "   done"
    assert "plain" in _format_output(["plain"])


def test_send_request_shows_approve_and_reject_buttons() -> None:
    bot, tx = _bot()
    _msg(bot, "send an email to anna@example.com")
    buttons = tx.sent[0].buttons
    assert buttons is not None
    labels = [b.label for row in buttons for b in row]
    assert any("approve" in label.lower() for label in labels)
    assert any("reject" in label.lower() for label in labels)


def test_format_arguments_is_empty_for_no_arguments() -> None:
    from ops_assistant.telegram.bot import _format_arguments

    assert _format_arguments({}) == ""


def test_format_arguments_truncates_long_values() -> None:
    from ops_assistant.telegram.bot import _format_arguments

    out = _format_arguments({"to": "a@b.c", "body": "x" * 300})
    assert "a@b.c" in out
    assert "…" in out and "x" * 300 not in out  # long body shown but truncated


def test_send_approval_prompt_shows_the_concrete_recipient() -> None:
    # Informed consent: the approver must see who the email goes to, not just the
    # tool name. The sandbox plan sends to anna@example.com.
    bot, tx = _bot()
    _msg(bot, "send an email to anna@example.com")
    body = tx.sent[0].text
    assert "anna@example.com" in body
    assert "to:" in body.lower()


def test_approve_callback_executes_and_edits_the_message() -> None:
    bot, tx = _bot()
    _msg(bot, "send an email to anna@example.com")
    approve_data = tx.sent[0].buttons[0][0].callback_data  # type: ignore[index]

    bot.handle_callback(
        callback_id="cb1",
        chat_id=100,
        message_id=55,
        user_id=1,
        user_name="roman",
        data=approve_data,
    )
    assert len(tx.edits) == 1
    assert "completed" in tx.edits[0].text.lower()
    assert tx.answered == [("cb1", "Approved")]


def test_reject_callback_edits_to_rejected() -> None:
    bot, tx = _bot()
    _msg(bot, "send an email to anna@example.com")
    reject_data = tx.sent[0].buttons[0][1].callback_data  # type: ignore[index]

    bot.handle_callback(
        callback_id="cb2",
        chat_id=100,
        message_id=55,
        user_id=1,
        user_name="roman",
        data=reject_data,
    )
    assert "rejected" in tx.edits[0].text.lower()
    assert tx.answered == [("cb2", "Rejected")]


def test_callback_on_already_decided_approval_is_handled_gracefully() -> None:
    bot, tx = _bot()
    _msg(bot, "send an email to anna@example.com")
    data = tx.sent[0].buttons[0][0].callback_data  # type: ignore[index]
    common = dict(callback_id="cb", chat_id=100, message_id=55, user_id=1, user_name="roman")
    bot.handle_callback(data=data, **common)  # type: ignore[arg-type]
    bot.handle_callback(data=data, **common)  # type: ignore[arg-type]  # second time: workflow done
    # No crash; the second callback answered with an explanatory toast, not "Approved".
    assert tx.answered[-1][0] == "cb"
    assert tx.answered[-1][1] != "Approved"


def test_unknown_callback_action_is_ignored_safely() -> None:
    bot, tx = _bot()
    bot.handle_callback(
        callback_id="cbx", chat_id=100, message_id=1, user_id=1, user_name="roman", data="zz:abc"
    )
    assert tx.answered and tx.answered[0][0] == "cbx"
    assert not tx.edits


def test_clarification_request_is_reported() -> None:
    bot, tx = _bot()
    _msg(bot, "send an update")  # no recipient -> clarification
    assert "?" in tx.sent[0].text


def test_dispatch_routes_a_message_update() -> None:
    bot, tx = _bot()
    from ops_assistant.telegram.runner import dispatch_update

    dispatch_update(
        bot,
        {
            "update_id": 1,
            "message": {
                "text": "find free time",
                "chat": {"id": 100},
                "from": {"id": 7, "username": "roman"},
            },
        },
    )
    assert len(tx.sent) == 1
    assert "completed" in tx.sent[0].text.lower()


def test_dispatch_routes_a_callback_update() -> None:
    bot, tx = _bot()
    from ops_assistant.telegram.runner import dispatch_update

    _msg(bot, "send an email to anna@example.com")
    data = tx.sent[0].buttons[0][0].callback_data  # type: ignore[index]
    dispatch_update(
        bot,
        {
            "update_id": 2,
            "callback_query": {
                "id": "cbz",
                "data": data,
                "from": {"id": 7, "username": "roman"},
                "message": {"message_id": 9, "chat": {"id": 100}},
            },
        },
    )
    assert len(tx.edits) == 1
    assert tx.answered == [("cbz", "Approved")]


def test_dispatch_ignores_non_text_and_malformed_updates() -> None:
    bot, tx = _bot()
    from ops_assistant.telegram.runner import dispatch_update

    dispatch_update(
        bot, {"message": {"chat": {"id": 1}, "from": {"id": 2}}}
    )  # no text (e.g. photo)
    dispatch_update(bot, {"message": {"text": "hi"}})  # missing chat/from
    dispatch_update(bot, {"callback_query": {"id": "x"}})  # missing message/from
    dispatch_update(bot, {"edited_message": {"text": "hi"}})  # update type we don't handle
    assert tx.sent == [] and tx.edits == [] and tx.answered == []


def test_allowlist_blocks_unlisted_users() -> None:
    bot, tx = _bot(allowed_users=frozenset({999}))
    _msg(bot, "find free time", user_id=1)  # not in allowlist
    assert len(tx.sent) == 1
    assert "authorized" in tx.sent[0].text.lower()
    # and a listed user is served
    bot.handle_message(chat_id=100, user_id=999, user_name="roman", text="find free time")
    assert "completed" in tx.sent[1].text.lower()


def test_allowlist_blocks_callback_from_unlisted_user() -> None:
    bot, tx = _bot(allowed_users=frozenset({999}))
    bot.handle_callback(
        callback_id="cb", chat_id=100, message_id=1, user_id=1, user_name="x", data="a:abc"
    )
    assert tx.answered and "authorized" in tx.answered[0][1].lower()
    assert not tx.edits


def test_handle_message_reports_a_refused_plan_instead_of_ghosting() -> None:
    # When the server refuses an unsafe plan, submit() raises. The user must get a
    # reply, not silence — the refusal is the safety layer working.
    from ops_assistant.errors import PlanValidationError

    class _Refuser:
        def submit(self, *, text: str, user: str, source: str) -> object:
            raise PlanValidationError("that tool is not available")

    tx = FakeTransport()
    bot = TelegramBot(_Refuser(), tx)  # type: ignore[arg-type]
    bot.handle_message(chat_id=1, user_id=1, user_name="roman", text="do something unsupported")
    assert len(tx.sent) == 1
    assert "couldn't safely" in tx.sent[0].text.lower()
    assert "that tool is not available" in tx.sent[0].text


def test_rate_limiter_blocks_a_burst_without_calling_the_service() -> None:
    from ops_assistant.telegram.bot import RATE_LIMITED
    from ops_assistant.telegram.ratelimit import RateLimiter

    limiter = RateLimiter(max_events=1, window_seconds=60.0, clock=lambda: 0.0)
    bot, tx = _bot(rate_limiter=limiter)
    _msg(bot, "find free time")  # first request: served
    _msg(bot, "find free time")  # second within the window: rate-limited
    assert "completed" in tx.sent[0].text.lower()
    assert tx.sent[1].text == RATE_LIMITED
    assert tx.sent[1].buttons is None


def test_rate_limiter_does_not_count_start() -> None:
    from ops_assistant.telegram.ratelimit import RateLimiter

    limiter = RateLimiter(max_events=1, window_seconds=60.0, clock=lambda: 0.0)
    bot, tx = _bot(rate_limiter=limiter)
    _msg(bot, "/start")  # must not consume the budget
    _msg(bot, "find free time")  # still the first real request -> served
    assert "completed" in tx.sent[1].text.lower()
