# Architecture

A one-page map of how a request becomes a controlled action. For the *why* behind
the load-bearing decisions, see the [ADRs](adr/).

## The path of a request

```
request ─▶ Planner ─▶ Policy engine ─▶ Orchestrator ─▶ Tool gateway ─▶ tools
          (LLM,        (re-derives      (state machine,   (idempotent,
           untrusted)   risk; gates)     approvals)        single chokepoint)
                                              │
                                              ▼
                                        Append-only audit
```

1. **Planner** (`planner/`) — an untrusted LLM turns free text into a structured
   `Plan` (validated, repaired once, fails closed). A keyless `DemoPlanner` is the
   fallback.
2. **Policy engine** (`policy.py`) — the security core. Re-derives each step's risk
   from the registry, rejects unknown/disabled/duplicate/cyclic plans and
   undeclared data-flow references, and decides auto vs. approval. Emits a
   `ValidatedPlan`.
3. **Orchestrator** (`service.py`) — guarded state machines. Auto-runs read-only
   steps, gates the rest behind single-use, plan-bound approvals, resolves
   `{{step.field}}` references, and settles the workflow. Holds an `RLock`;
   guard-before-mutate.
4. **Tool gateway** (`gateway.py`) — the single execution chokepoint; idempotent per
   `(workflow, step)`.
5. **Audit** (`audit.py`) — append-only event log of every decision and action.

## Layers & seams

- **Models** (`models.py`) — `RiskTier`, `Plan`, `plan_fingerprint`.
- **Tools** (`tools/`) — the registry is the *server-side source of truth for risk*.
  `sandbox.py` (keyless) and `gworkspace/` (real Gmail/Calendar) register the **same
  tool names and tiers**, so the control machinery is identical whether or not real
  credentials are present.
- **Storage seams** (`store.py`, `approval.py`, `gateway.py`, `audit.py`) — each is a
  `Protocol` with an in-memory and a Postgres implementation (`persistence/`). The
  core imports nothing from `persistence`; `factory.py` wires Postgres when
  `OPS_DATABASE_URL` is set. See [ADR-0002](adr/0002-storage-seams.md).
- **Surfaces** — a FastAPI app (`api/`), a Telegram bot (`telegram/`), and an n8n
  webhook client (`n8n/`). All go through the same `OpsService`.
- **Knowledge** (`knowledge/`) — TF-IDF retrieval with citations (`knowledge.search`).
- **Observability** (`observability.py`) — folds the audit log into `/metrics`.

## Invariants (each pinned by a test)

- The model's `claimed_risk` can never change the decision — [ADR-0001](adr/0001-server-owns-risk.md).
- What a human approves is exactly what executes — [ADR-0003](adr/0003-dataflow-declared-deps.md).
- The audit trail is append-only and never stores a full message body —
  [ADR-0004](adr/0004-append-only-audit-and-redaction.md).
- No external side-effect runs without a human tap.

## Where the code lives

```
ops_assistant/
  models.py policy.py service.py gateway.py approval.py audit.py  # core
  dataflow.py workflow.py state.py store.py config.py factory.py
  planner/  tools/  gworkspace/  knowledge/  n8n/  telegram/  api/  persistence/
```
