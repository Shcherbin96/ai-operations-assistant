"""A deterministic, offline planner.

It recognizes a handful of intents by keyword and emits a fixed plan for each, so
the system runs end-to-end with no LLM and no keys. It is intentionally simple:
its job is to exercise the *control* machinery (validation, approval, execution,
audit), which is where this project's value lives. The LLM planner arrives in
Stage 4 behind the same interface.
"""

from __future__ import annotations

import re

from ops_assistant.models import OperationRequest, Plan, PlanStep

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


class DemoPlanner:
    def plan(self, request: OperationRequest) -> Plan:
        text = request.text.lower()

        if "free time" in text or "free slot" in text:
            return Plan(
                summary="Find free time on the calendar",
                steps=[
                    PlanStep(
                        id="s1",
                        tool="calendar.find_free_time",
                        arguments={"range": "tomorrow"},
                        reason="The user asked for available time.",
                    )
                ],
            )

        if "draft" in text or "reply" in text or "replies" in text:
            return Plan(
                summary="Find recent customer emails and prepare drafts (nothing is sent)",
                steps=[
                    PlanStep(
                        id="s1",
                        tool="email.search",
                        arguments={"query": "newer_than:3d"},
                        reason="Find recent inbound messages.",
                    ),
                    PlanStep(
                        id="s2",
                        tool="email.create_draft",
                        arguments={"to": "anna@example.com", "body": "Draft reply"},
                        depends_on=["s1"],
                        reason="Prepare a reply the user can review before sending.",
                    ),
                ],
            )

        if "send" in text:
            match = _EMAIL_RE.search(request.text)
            if match is None:
                return Plan(
                    summary="Send an email",
                    requires_clarification=True,
                    clarification_question=(
                        "Who should I send this email to? Please give a recipient address."
                    ),
                )
            recipient = match.group(0)
            return Plan(
                summary=f"Send an email to {recipient}",
                steps=[
                    PlanStep(
                        id="s1",
                        tool="email.send",
                        arguments={"to": recipient, "body": "..."},
                        reason="The user asked to send an email.",
                    )
                ],
            )

        return Plan(
            summary="Unrecognized request",
            requires_clarification=True,
            clarification_question=(
                "I couldn't map that to an action I support yet. Could you rephrase — "
                "for example, ask me to check emails, draft replies, or find free time?"
            ),
        )
