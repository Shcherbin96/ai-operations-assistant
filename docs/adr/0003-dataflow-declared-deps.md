# ADR-0003 — Data-flow references must be declared dependencies

**Status:** accepted (v1.3.0)

## Context

Inter-step data-flow lets a step use an earlier step's real output, e.g.
`email.send(to="{{s1.from}}")`. The reference is resolved from upstream tool output —
which, for an inbound email, is **attacker-influenceable**. Two failure modes:

1. The human approves a placeholder (`{{s1.from}}`) while the *resolved* value — the
   real recipient — appears on no surface they saw. That breaks informed consent.
2. Approval readiness gates on a step's declared `depends_on`, but execution
   resolves against *all* succeeded outputs. A reference to an **undeclared** step
   could be an unresolved placeholder at approval yet resolve to a real value at
   execution — a redirect the preview never showed.

## Decision

The policy engine requires every `{{step.field}}` reference to name a step declared
in that step's `depends_on` (`referenced_steps` + the check in `policy.py`). A token
that names no step (a mail-merge `{{name}}`) is left literal, not treated as a
reference. The orchestrator resolves references when **building the approval**, so the
stored approval and every surface (Telegram, API) show the resolved arguments.

## Consequences

- Approval-time and execution-time resolution draw on the *same* frozen, declared,
  already-succeeded outputs → **what the human approves is what runs**, structurally.
- An undeclared-reference redirect is refused at validation (fail-closed).
- The plan-bound approval fingerprint needn't fold in resolved values, since
  resolution is now deterministic within the workflow.
- Pinned by an injection e2e test and an undeclared-reference-refused test. An
  adversarial review of the change also caught (and we fixed) an over-broad version
  of the rule that rejected legitimate `{{name}}` templates.
