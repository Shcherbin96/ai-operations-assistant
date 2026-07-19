"""``python -m ops_assistant.gworkspace auth`` runs the one-time Google consent
flow (opens your browser; you approve) and caches the token."""

from __future__ import annotations

import sys

from ops_assistant.config import get_settings
from ops_assistant.gworkspace.auth import run_auth_flow


def main() -> None:  # pragma: no cover - opens a browser for user consent
    if len(sys.argv) < 2 or sys.argv[1] != "auth":
        raise SystemExit("usage: python -m ops_assistant.gworkspace auth")
    settings = get_settings()
    if not settings.google_client_secrets:
        raise SystemExit("Set OPS_GOOGLE_CLIENT_SECRETS to the OAuth client-secrets JSON path.")
    run_auth_flow(settings.google_client_secrets, settings.google_token_path)
    print(f"Authorized. Token cached at {settings.google_token_path}.")


if __name__ == "__main__":  # pragma: no cover
    main()
