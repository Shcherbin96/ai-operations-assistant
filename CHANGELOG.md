# Changelog

## 1.0.0 — 2026-07-19

First stable release. A natural-language operations assistant built around one
rule: **the model proposes the plan; the server decides what runs.**

### Capabilities

- **Control loop** — a plain-language request becomes a structured plan; the
  server re-derives the real risk tier of every step from its own registry (the
  model cannot lower it), auto-executes read-only steps, and gates every external
  side-effect behind human approval. Guarded state machines, single-use
  plan-bound approvals, an idempotent tool gateway, and an append-only audit
  trail.
- **LLM planner** — an OpenAI-compatible model (Gemini / OpenRouter / …) produces
  the structured plan; output is validated, repaired once, and fails closed. The
  deterministic demo planner is the keyless fallback.
- **Persistence** — Postgres behind the same interface, so a paused workflow
  survives a restart. Append-only audit enforced by triggers, idempotency via
  `ON CONFLICT`, and optimistic locking on a version column.
- **Telegram bot** — send a request, see the plan and its results, Approve /
  Reject with inline buttons.
- **Gmail & Calendar** — real read/draft/send and event tools under the same
  names and risk tiers as the sandbox; sends and event changes stay gated.
- **Knowledge base** — `knowledge.search` answers policy questions with citations.
- **n8n** — trigger allowlisted workflows via signed webhooks, gated by approval.
- **Evals & observability** — an offline golden-scenario regression gate, live
  planner evals (tool selection + prompt-injection resistance), and metrics
  folded from the audit trail (`GET /metrics`).

### Quality

186 unit tests at 100% coverage, 10 Postgres integration tests, strict mypy,
ruff, and a 3-OS CI matrix. Every stage was built test-first and hardened by an
adversarial review. Docker + Compose for a one-command run.

### Known limitations

- No data-flow between plan steps yet (a step's arguments are static from the
  plan, not filled from an earlier step's output).
- The live Gmail/Calendar send/create paths and the n8n webhook are exercised by
  running, not in CI (they need real credentials / an n8n instance).
- Retrieval is TF-IDF; embeddings + pgvector are a documented upgrade behind the
  same interface.
