"""Google OAuth (Desktop / installed-app flow).

The user runs the flow once (``python -m ops_assistant.gworkspace auth``) and
consents in their own browser — this code never sees their Google password. The
resulting token is cached in ``token.json`` (gitignored) and refreshed silently.
"""

from __future__ import annotations

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# Least-privilege: read the mailbox, manage drafts + send, manage calendar events.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.events",
]


def load_credentials(
    client_secrets: str, token_path: str = "token.json"
) -> Credentials | None:  # pragma: no cover - OAuth/token I/O
    """Return valid credentials from a cached token (refreshing if needed), or None."""
    path = Path(token_path)
    if not path.exists():
        return None
    creds = Credentials.from_authorized_user_file(str(path), SCOPES)
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        path.write_text(creds.to_json())
        return creds
    return None


def run_auth_flow(
    client_secrets: str, token_path: str = "token.json"
) -> Credentials:  # pragma: no cover - opens a browser for user consent
    """Run the one-time consent flow and cache the token."""
    flow = InstalledAppFlow.from_client_secrets_file(client_secrets, SCOPES)
    creds = flow.run_local_server(port=0)
    Path(token_path).write_text(creds.to_json())
    return creds
