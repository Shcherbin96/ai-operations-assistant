# ADR-0001 — The server owns risk; the model cannot lower it

**Status:** accepted

## Context

The system lets an LLM plan real actions (send email, create events). If the model
decided what was safe to run, a hallucination or a prompt injection could auto-run a
destructive or external action. Many "AI agent" designs let the model self-report
confidence or call tools directly — which places trust in exactly the wrong place.

## Decision

The model returns a plan as structured data and may *label* each step's risk, but
that label is **advisory only**. The policy engine re-derives the real risk tier of
every step from the server's own tool registry and decides auto-execute vs.
human-approval from *that*. `claimed_risk` is compared to the truth solely to flag
drift; it can never move the decision. Disabled tiers (`destructive`) are refused
outright.

## Consequences

- A fully subverted planner still cannot auto-run a gated action — the worst it can
  do is propose one, which the server gates. This is the project's headline safety
  property.
- Risk lives in one place (the registry), so adding a tool means classifying it once.
- The guarantee is pinned across the *whole* registry, not one example
  (`tests/test_policy.py::test_server_owns_risk_for_every_tool_and_every_claim`), so a
  future tool can't silently regress it.
- Cost: the model can't "optimize" by skipping approval; that is the point.
