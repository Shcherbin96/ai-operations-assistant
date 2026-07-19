# Deploying the always-on demo bot

This runs the Telegram bot 24/7 as a public demo so a reviewer can try it any
time — without it dying when a laptop closes. It deploys to [Fly.io](https://fly.io)
as a single long-polling worker.

## What this deployment is (and isn't)

- **Sandbox only.** The demo never sets `OPS_GOOGLE_CLIENT_SECRETS`, so the
  Gmail/Calendar tools fall back to keyless mocks. **A stranger messaging the demo
  cannot reach a real inbox or calendar.** Your private, real-Gmail bot stays on
  your own machine — see the note on separate bots below.
- **A separate bot.** Telegram allows only one long-poller per token, so the
  public demo must use a **different** BotFather bot than your private one — they
  would 409-conflict otherwise. Making a *new* bot also means the open demo can
  never be pointed at real Gmail by a config slip.
- **State is in-memory.** No database — a redeploy resets workflows. That's fine
  for a demo (persistence is already proven by the Postgres integration tests).

## Two modes — pick before you deploy

| | LLM planner (recommended) | Zero-cost |
|---|---|---|
| Plans produced by | a real LLM (the project's whole thesis) | the deterministic demo planner |
| Cost / abuse risk | pays per request → **needs a spend cap** | none — no paid calls at all |
| Config | set the `OPS_LLM_*` secrets below | leave `OPS_LLM_*` unset |

The rate limiter (5 req/user/60s, in `fly.toml`) protects the LLM mode and is
harmless in zero-cost mode.

## ⚠️ Do these in order — the first two BEFORE the bot is public

Because the bot is open to anyone, an unbounded LLM bill is the real risk. The
in-app rate limiter is first-line only; the hard backstop is provider-side.

1. **Rotate the OpenRouter key.** The old key was pasted into a chat — treat it as
   burned. Create a fresh key at <https://openrouter.ai/keys>. *(Skip if you're
   deploying in zero-cost mode.)*
2. **Set a hard spend cap** on that key (a credit limit) at OpenRouter, so the
   worst case is bounded no matter what. *(Skip in zero-cost mode.)*
3. **Create a new demo bot** — message [@BotFather](https://t.me/BotFather),
   `/newbot`, and copy its token. Do **not** reuse your private bot's token.

Only after 1–3 should you deploy and let people use it.

## Cost reality (new information — decide if it's worth it)

Always-on is **not free**. A `shared-cpu-1x` / 256 MB machine running 24/7 is
roughly **a few dollars a month** on Fly. Most "free tiers" elsewhere *sleep* a
worker with no HTTP port after idle — for a long-polling bot that means it's dead
exactly when someone tries it, which defeats the point. This `fly.toml` avoids
that (no auto-stop), which is why it costs a little.

If a few $/month isn't worth it, a recorded GIF of the bot in the README gives
reviewers "see it work" at zero cost — say the word and I'll add one instead.

## Deploy (you run these — I can't create the account or hold secrets)

```bash
# One-time: install flyctl and sign in (needs a card on file; pay-as-you-go).
brew install flyctl        # or: curl -L https://fly.io/install.sh | sh
fly auth login

cd ~/dev/ai-operations-assistant

# Create the app from the committed fly.toml. Pick a unique name when prompted;
# --no-deploy so nothing runs before the secrets are set.
fly launch --no-deploy --copy-config --name <your-unique-app-name>

# Secrets (never put these in fly.toml). LLM ones only for the recommended mode:
fly secrets set OPS_TELEGRAM_TOKEN="<token from the NEW demo bot>"
fly secrets set OPS_LLM_API_KEY="<your freshly-rotated OpenRouter key>"
fly secrets set OPS_LLM_BASE_URL="https://openrouter.ai/api/v1"
fly secrets set OPS_LLM_MODEL="google/gemini-2.5-flash"

# Note: do NOT set OPS_TELEGRAM_ALLOWED_USERS (open demo) and do NOT set
# OPS_GOOGLE_CLIENT_SECRETS (keeps it sandbox-only).

fly deploy
```

## Verify

```bash
fly status                       # one machine, state "started"
fly logs                         # look for "AI Operations Assistant bot is polling."
```

Then message the new bot on Telegram: `/start`, then e.g. *"find free time
tomorrow"* (auto-runs, read-only) and *"send an email to anna@example.com"*
(pauses for Approve/Reject — the whole point). Fire six quick requests to see the
rate-limiter reply.

## Teardown

```bash
fly apps destroy <your-app-name>
```

Deleting the app stops all billing.
