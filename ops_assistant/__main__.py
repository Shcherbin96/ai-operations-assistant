"""``python -m ops_assistant`` serves the API with the keyless sandbox tools."""

from __future__ import annotations

import uvicorn

from ops_assistant.api.app import app
from ops_assistant.config import get_settings


def main() -> None:  # pragma: no cover - server entrypoint
    settings = get_settings()
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":  # pragma: no cover
    main()
