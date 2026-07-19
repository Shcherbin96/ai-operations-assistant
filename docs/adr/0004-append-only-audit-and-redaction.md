# ADR-0004 — Append-only audit + provenance-aware redaction

**Status:** accepted (redaction refined in v1.3.0)

## Context

An operations assistant that touches real systems needs a trustworthy record: what
was requested, what was decided, who approved, what ran. "Trustworthy" means
tamper-evident — an editable log proves nothing. But an immutable log that stores
full email bodies would permanently retain PII that can never be scrubbed.

## Decision

The audit trail is **append-only**, enforced at the database by triggers that reject
`UPDATE`, `DELETE`, and `TRUNCATE` on `audit_events` (`persistence/schema.py`) — not
merely by application convention. It records the resolved arguments and a result
digest, so it can answer "who did this send actually go to?".

Redaction is **provenance-aware** (`_redact_for_audit`): a value substituted from a
`{{step.field}}` reference is tool output and is reduced to a shape summary —
*except* routing fields (the recipient is the forensic point). Literal, plan-authored
text is kept (capped). Body-like keys are always redacted; nested structures are
never dumped verbatim. The Telegram approval preview uses a *separate*, non-redacting
formatter, because informed consent requires the human to see the real body.

## Consequences

- The immutable record contains the forensic "who/what" without ever persisting a
  message body — the two audiences (immutable log vs. human preview) get different
  treatment by design.
- Because rows can't be scrubbed, redaction errs toward revealing less.
- An adversarial review found that an earlier key-based redactor leaked a body via a
  whole-output reference, a list value, or a renamed key; the provenance-aware
  version closes all three. Pinned by tests in `tests/test_service.py`.
