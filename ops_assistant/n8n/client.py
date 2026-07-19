"""n8n client: trigger a workflow through a signed webhook.

Every request is HMAC-signed with a shared secret so the n8n side can reject
anything not from this app. The live client is I/O; the tool layer is tested with
a fake.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Protocol


class N8nClient(Protocol):
    def trigger(self, workflow: str, payload: dict[str, object]) -> dict[str, object]: ...


def sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
