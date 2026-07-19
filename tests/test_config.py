"""Settings load from defaults and from the OPS_ env prefix."""

import pytest

from ops_assistant.config import Settings


def test_defaults() -> None:
    settings = Settings()
    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.approval_ttl_seconds == 3600


def test_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPS_PORT", "9999")
    assert Settings().port == 9999


def test_get_settings_is_cached() -> None:
    from ops_assistant.config import get_settings

    assert get_settings() is get_settings()
