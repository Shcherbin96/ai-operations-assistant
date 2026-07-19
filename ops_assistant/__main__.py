"""``python -m ops_assistant`` serves the API.

With ``OPS_DATABASE_URL`` set, state is persisted in Postgres; without it, the app
runs fully in-memory over the keyless sandbox tools.
"""

from __future__ import annotations

import uvicorn

from ops_assistant.api.app import create_app
from ops_assistant.config import get_settings


def main() -> None:  # pragma: no cover - server entrypoint
    from ops_assistant.factory import service_from_settings

    settings = get_settings()
    application = create_app(service_from_settings(settings))
    uvicorn.run(application, host=settings.host, port=settings.port)


if __name__ == "__main__":  # pragma: no cover
    main()
