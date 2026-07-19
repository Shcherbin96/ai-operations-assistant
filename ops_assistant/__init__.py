"""AI Operations Assistant — the model proposes the plan; the server decides what runs."""

from __future__ import annotations

import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def _resolve_version() -> str:
    """Single source of truth: the version in ``pyproject.toml``.

    Prefer installed distribution metadata (if the project is ever packaged), then
    read ``pyproject.toml`` directly — it ships in the repo and is copied into the
    Docker image, and ``[tool.uv] package = false`` means there is no dist metadata
    to read otherwise.
    """
    try:
        return version("ai-operations-assistant")
    except PackageNotFoundError:
        pass
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    try:
        with pyproject.open("rb") as handle:
            return str(tomllib.load(handle)["project"]["version"])
    except (OSError, KeyError):  # pragma: no cover - only if pyproject is absent/malformed
        return "0.0.0"


__version__ = _resolve_version()
