"""Live n8n client over httpx. I/O only; the tool layer is tested with a fake."""

from __future__ import annotations

import json

import httpx

from ops_assistant.n8n.client import sign


class HttpN8nClient:
    def __init__(self, base_url: str, secret: str, client: httpx.Client | None = None) -> None:
        self._base = base_url.rstrip("/")
        self._secret = secret
        self._client = client or httpx.Client(timeout=30.0)

    def trigger(self, workflow: str, payload: dict[str, object]) -> dict[str, object]:
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
        response = self._client.post(
            f"{self._base}/webhook/{workflow}",
            content=body,
            headers={"Content-Type": "application/json", "X-Signature": sign(body, self._secret)},
        )
        response.raise_for_status()
        return {"workflow": workflow, "status": "triggered"}
