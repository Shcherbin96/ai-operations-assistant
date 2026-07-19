"""The LLM planner: free-form request -> structured Plan, validated and fail-closed.

Driven by a fake LLM client, so the planning logic (prompt, JSON extraction,
schema validation, repair loop, fail-closed fallback) is tested without a key or
network. The live OpenAI-compatible client is I/O.
"""

from dataclasses import dataclass, field

from ops_assistant.models import OperationRequest
from ops_assistant.planner.base import Planner
from ops_assistant.planner.llm import LLMPlanner
from ops_assistant.tools.sandbox import build_sandbox_registry


@dataclass
class FakeLLM:
    """Returns queued replies in order; records the prompts it was given."""

    replies: list[str]
    calls: list[tuple[str, str]] = field(default_factory=list)

    def complete(self, *, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self.replies.pop(0) if self.replies else ""


def _req(text: str) -> OperationRequest:
    return OperationRequest(id="r1", text=text, user="u1", source="test")


def _planner(*replies: str) -> tuple[LLMPlanner, FakeLLM]:
    llm = FakeLLM(list(replies))
    return LLMPlanner(llm, build_sandbox_registry()), llm


def test_llm_planner_is_a_planner() -> None:
    planner, _ = _planner("{}")
    assert isinstance(planner, Planner)


def test_valid_json_becomes_a_plan() -> None:
    reply = (
        '{"summary": "Find free time", "requires_clarification": false, '
        '"steps": [{"id": "s1", "tool": "calendar.find_free_time", "arguments": {}, '
        '"depends_on": [], "reason": "user asked"}]}'
    )
    planner, _ = _planner(reply)
    plan = planner.plan(_req("when am I free?"))
    assert plan.summary == "Find free time"
    assert [s.tool for s in plan.steps] == ["calendar.find_free_time"]


def test_clarification_plan_is_passed_through() -> None:
    reply = (
        '{"summary": "Send email", "requires_clarification": true, '
        '"clarification_question": "To whom?", "steps": []}'
    )
    planner, _ = _planner(reply)
    plan = planner.plan(_req("send an email"))
    assert plan.requires_clarification is True
    assert plan.clarification_question == "To whom?"


def test_markdown_fenced_json_is_extracted() -> None:
    reply = '```json\n{"summary": "s", "requires_clarification": false, "steps": []}\n```'
    planner, _ = _planner(reply)
    assert planner.plan(_req("x")).summary == "s"


def test_invalid_json_is_repaired_on_retry() -> None:
    good = '{"summary": "ok", "requires_clarification": false, "steps": []}'
    planner, llm = _planner("not json at all", good)
    plan = planner.plan(_req("x"))
    assert plan.summary == "ok"
    assert len(llm.calls) == 2  # one repair attempt


def test_persistently_invalid_output_fails_closed() -> None:
    planner, _ = _planner("garbage", "still garbage", "and again")
    plan = planner.plan(_req("x"))
    # No crash, no fabricated steps: a safe clarification instead.
    assert plan.requires_clarification is True
    assert plan.steps == []


def test_system_prompt_lists_tools_and_forbids_injection() -> None:
    planner, llm = _planner('{"summary": "s", "requires_clarification": false, "steps": []}')
    planner.plan(_req("do something"))
    system = llm.calls[0][0]
    assert "calendar.find_free_time" in system  # tools are offered
    assert "email.send" in system
    assert "ignore" in system.lower()  # prompt-injection guardrail is stated
    assert "{{step_id.field}}" in system  # the data-flow reference syntax is documented


def test_client_failure_fails_closed_instead_of_crashing() -> None:
    class Boom:
        def complete(self, *, system: str, user: str) -> str:
            raise RuntimeError("provider unavailable")

    planner = LLMPlanner(Boom(), build_sandbox_registry())
    plan = planner.plan(_req("x"))  # must not raise
    assert plan.requires_clarification is True
    assert plan.steps == []


def test_non_string_client_reply_fails_closed() -> None:
    class Weird:
        def complete(self, *, system: str, user: str) -> str:
            return None  # type: ignore[return-value]

    planner = LLMPlanner(Weird(), build_sandbox_registry())
    assert planner.plan(_req("x")).requires_clarification is True


def test_schema_violation_does_not_become_a_plan() -> None:
    # Well-formed JSON but wrong shape (steps not a list) -> repaired/failed-closed.
    planner, _ = _planner('{"summary": 5, "steps": "nope"}', "also bad")
    plan = planner.plan(_req("x"))
    assert plan.requires_clarification is True
