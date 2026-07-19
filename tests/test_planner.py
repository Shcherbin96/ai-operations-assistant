"""The demo planner: deterministic intent -> plan mapping, no LLM, no network.

It lets the whole system run and be demoed with zero keys, and it seeds the golden
scenarios. A real LLM planner (Stage 4) implements the same ``Planner`` protocol.
"""

from ops_assistant.models import OperationRequest, Plan
from ops_assistant.planner.base import Planner
from ops_assistant.planner.demo import DemoPlanner


def _req(text: str) -> OperationRequest:
    return OperationRequest(id="r1", text=text, user="u1", source="test")


def test_demo_planner_is_a_planner() -> None:
    assert isinstance(DemoPlanner(), Planner)


def test_find_free_time_intent_is_read_only() -> None:
    plan = DemoPlanner().plan(_req("find some free time tomorrow"))
    assert [s.tool for s in plan.steps] == ["calendar.find_free_time"]
    assert plan.requires_clarification is False


def test_draft_intent_searches_then_drafts_and_never_sends() -> None:
    plan = DemoPlanner().plan(_req("check emails and draft replies, do not send anything"))
    tools = [s.tool for s in plan.steps]
    assert "email.search" in tools
    assert "email.create_draft" in tools
    assert "email.send" not in tools  # "do not send" is honored


def test_draft_step_depends_on_the_search_step() -> None:
    plan = DemoPlanner().plan(_req("draft replies to recent emails"))
    search = next(s for s in plan.steps if s.tool == "email.search")
    draft = next(s for s in plan.steps if s.tool == "email.create_draft")
    assert search.id in draft.depends_on


def test_send_intent_with_a_recipient_produces_a_send_step() -> None:
    plan = DemoPlanner().plan(_req("send an email to anna@example.com about the invoice"))
    send = next((s for s in plan.steps if s.tool == "email.send"), None)
    assert send is not None
    assert send.arguments.get("to") == "anna@example.com"


def test_send_intent_without_a_recipient_asks_for_clarification() -> None:
    plan = DemoPlanner().plan(_req("send an email about the invoice"))
    assert plan.requires_clarification is True
    assert plan.clarification_question
    assert plan.steps == []


def test_unrecognized_request_asks_for_clarification() -> None:
    plan = DemoPlanner().plan(_req("asdf qwerty zxcv"))
    assert plan.requires_clarification is True
    assert isinstance(plan, Plan)
