"""``python -m ops_assistant`` serves the API.

With ``OPS_DATABASE_URL`` set, state is persisted in Postgres; without it, the app
runs fully in-memory over the keyless sandbox tools.
"""

from __future__ import annotations

import uvicorn

from ops_assistant.api.app import create_app
from ops_assistant.config import get_settings


def main() -> None:  # pragma: no cover - server entrypoint
    settings = get_settings()
    if settings.database_url:
        from ops_assistant.factory import build_engine, make_postgres_service
        from ops_assistant.persistence.schema import create_schema

        engine = build_engine(settings.database_url)
        create_schema(engine)
        application = create_app(make_postgres_service(engine))
    else:
        application = create_app()
    uvicorn.run(application, host=settings.host, port=settings.port)


if __name__ == "__main__":  # pragma: no cover
    main()
