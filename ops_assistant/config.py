"""Runtime configuration, driven by environment variables (prefix ``OPS_``).

Secrets never live in code. Local development reads a ``.env`` file; production
supplies real environment variables or a secret store.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OPS_", env_file=".env", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8000
    approval_ttl_seconds: int = 3600
    # Optional deployment-wide tool allowlist (comma-separated tool names). Empty ->
    # every registered tool is permitted. A plan using a tool outside the list is
    # refused by the policy engine.
    allowed_tools: str = ""
    # Postgres DSN. Unset -> the app runs fully in-memory (keyless demo).
    database_url: str | None = None
    # Telegram bot token (from @BotFather). Unset -> the Telegram bot is disabled.
    telegram_token: str | None = None
    # Optional allowlist of Telegram user ids (comma-separated). Empty -> open (demo).
    telegram_allowed_users: str = ""
    # Public-demo guard: max requests per user per window. 0 -> no rate limiting.
    telegram_rate_limit: int = 0
    telegram_rate_window_seconds: float = 60.0
    # Google OAuth (Stage 4). Unset -> Gmail/Calendar tools fall back to the sandbox.
    google_client_secrets: str | None = None
    google_token_path: str = "token.json"
    # LLM planner (OpenAI-compatible). Unset -> the deterministic demo planner.
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    # Knowledge base directory (markdown). Adds the knowledge.search tool if non-empty.
    knowledge_dir: str = "knowledge_base"
    # n8n execution layer. All three set -> the n8n.run tool (allowlisted, gated).
    n8n_base_url: str | None = None
    n8n_secret: str | None = None
    n8n_workflows: str = ""  # comma-separated workflow allowlist


@lru_cache
def get_settings() -> Settings:
    return Settings()
