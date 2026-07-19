"""Telegram bot logic — pure, transport-agnostic, and fully testable.

The bot turns a chat message into ``OpsService.submit`` and renders the resulting
workflow (plan, per-step status, and inline Approve/Reject buttons for anything
awaiting approval). A button press becomes ``approve_pending`` / ``reject_pending``
and edits the original message with the outcome.

It knows nothing about HTTP: everything goes through a :class:`TelegramTransport`,
so tests drive it with a fake and the live client is a thin adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ops_assistant.errors import OpsAssistantError
from ops_assistant.models import StepStatus
from ops_assistant.service import OpsService, WorkflowView

WELCOME = (
    "👋 I'm your AI Operations Assistant.\n\n"
    "Tell me what to do in plain language — for example:\n"
    "• check emails and draft replies\n"
    "• find free time tomorrow\n"
    "• send an email to anna@example.com\n\n"
    "I'll plan it, run what's safe, and ask you to approve anything that leaves "
    "the building. Nothing external happens without your tap."
)

_STATUS_EMOJI: dict[StepStatus, str] = {
    StepStatus.SUCCEEDED: "✅",
    StepStatus.FAILED: "❌",
    StepStatus.SKIPPED: "⏭️",
    StepStatus.REJECTED: "🚫",
    StepStatus.AWAITING_APPROVAL: "⏸️",
    StepStatus.RUNNING: "⏳",
    StepStatus.PENDING: "•",
    StepStatus.BLOCKED: "•",
}


@dataclass(frozen=True)
class Button:
    label: str
    callback_data: str


class TelegramTransport(Protocol):
    def send_message(
        self, chat_id: int, text: str, buttons: list[list[Button]] | None = None
    ) -> None: ...

    def edit_message(
        self, chat_id: int, message_id: int, text: str, buttons: list[list[Button]] | None = None
    ) -> None: ...

    def answer_callback(self, callback_id: str, text: str) -> None: ...


def _summarize_item(item: object) -> str:
    """A one-line, human-readable summary of one result item."""
    if isinstance(item, dict):
        if "from" in item and "subject" in item:
            return f"{item['from']} — {item['subject']}"
        if "title" in item and "start" in item:
            return f"{item['title']} ({item['start']})"
        if "start" in item and "end" in item:
            return f"{item['start']} → {item['end']}"
        if "source" in item and "text" in item:
            return f"{item['source']} — {item['text']}"  # a cited knowledge snippet
        parts = [f"{k}: {v}" for k, v in item.items() if k not in ("answered", "id")]
        return ", ".join(parts) if parts else str(item)
    return str(item)


def _format_output(output: object) -> str:
    """Render a tool result so the user sees the answer, not just 'succeeded'."""
    if isinstance(output, list):
        if not output:
            return "   (nothing found)"
        lines = [f"   • {_summarize_item(item)}" for item in output[:5]]
        if len(output) > 5:
            lines.append(f"   … and {len(output) - 5} more")
        return "\n".join(lines)
    if isinstance(output, dict):
        return f"   {_summarize_item(output)}"
    return f"   {output}"


class TelegramBot:
    def __init__(
        self,
        service: OpsService,
        transport: TelegramTransport,
        allowed_users: frozenset[int] | None = None,
    ) -> None:
        self._svc = service
        self._tx = transport
        self._allowed = allowed_users or frozenset()

    def handle_message(self, *, chat_id: int, user_id: int, user_name: str, text: str) -> None:
        if not self._authorized(user_id):
            self._tx.send_message(chat_id, "⛔ You are not authorized to use this assistant.")
            return
        if text.strip() == "/start":
            self._tx.send_message(chat_id, WELCOME)
            return
        view = self._svc.submit(text=text, user=user_name, source="telegram")
        body, buttons = self._render(view)
        self._tx.send_message(chat_id, body, buttons)

    def handle_callback(
        self,
        *,
        callback_id: str,
        chat_id: int,
        message_id: int,
        user_id: int,
        user_name: str,
        data: str,
    ) -> None:
        if not self._authorized(user_id):
            self._tx.answer_callback(callback_id, "⛔ Not authorized")
            return

        action, _, approval_id = data.partition(":")
        try:
            if action == "a":
                view = self._svc.approve_pending(approval_id, actor=user_name)
                toast = "Approved"
            elif action == "r":
                view = self._svc.reject_pending(approval_id, actor=user_name)
                toast = "Rejected"
            else:
                self._tx.answer_callback(callback_id, "Unknown action")
                return
        except OpsAssistantError as exc:
            self._tx.answer_callback(callback_id, f"Couldn't do that: {exc.message}")
            return

        body, buttons = self._render(view)
        self._tx.edit_message(chat_id, message_id, body, buttons)
        self._tx.answer_callback(callback_id, toast)

    def _authorized(self, user_id: int) -> bool:
        return not self._allowed or user_id in self._allowed

    def _render(self, view: WorkflowView) -> tuple[str, list[list[Button]] | None]:
        # Plain text (no Markdown): the body contains underscores in tool/enum
        # names, so any markup parse_mode would 400. See HttpTelegramTransport.
        lines = [view.summary or "Request received"]
        lines.append(f"Status: {view.status.value}")

        if view.requires_clarification and view.clarification_question:
            lines.append("")
            lines.append(f"❓ {view.clarification_question}")

        for step in view.steps:
            emoji = _STATUS_EMOJI.get(step.status, "•")
            lines.append(f"{emoji} {step.tool} [{step.resolved_risk.value}] — {step.status.value}")
            if step.status is StepStatus.SUCCEEDED and step.output is not None:
                detail = _format_output(step.output)
                if detail:
                    lines.append(detail)

        buttons: list[list[Button]] | None = None
        if view.pending_approvals:
            buttons = [
                [
                    Button(f"✅ Approve: {a.tool}", f"a:{a.id}"),
                    Button("❌ Reject", f"r:{a.id}"),
                ]
                for a in view.pending_approvals
            ]

        return "\n".join(lines), buttons
