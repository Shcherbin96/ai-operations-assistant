"""Inter-step data-flow.

A plan step can reference an earlier step's output with ``{{step_id.field}}``
(dotted paths, array indices, and whole-output ``{{step_id}}`` are allowed). Before
a step runs, the executor resolves these against the outputs of the steps that
already succeeded — so a draft goes to the *actual* sender found by the search
step, not a placeholder. Unresolvable references are left literal (never silently
wrong).
"""

from __future__ import annotations

import re
from collections.abc import Mapping

_REF = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


def _parse_ref(inner: str) -> tuple[str, str | None]:
    match = re.match(r"(\w+)(.*)", inner.strip())
    if match is None:
        return "", None
    rest = match.group(2).lstrip(".")
    return match.group(1), rest or None


def _segments(path: str) -> list[str | int]:
    normalized = path.replace("[", ".").replace("]", "")
    return [int(p) if p.isdigit() else p for p in normalized.split(".") if p]


def _walk(current: object | None, path: str | None) -> object | None:
    if not path:
        return current
    for segment in _segments(path):
        if isinstance(segment, int):
            if isinstance(current, list) and -len(current) <= segment < len(current):
                current = current[segment]
            else:
                return None
        else:
            if isinstance(current, list):
                current = current[0] if current else None
            if isinstance(current, dict):
                current = current.get(segment)
            else:
                return None
    return current


def _lookup(outputs: Mapping[str, object], inner: str) -> object | None:
    step, path = _parse_ref(inner)
    root = outputs.get(step)
    if root is None:
        return None
    exact = _walk(root, path)
    if exact is not None:
        return exact
    # Fallback for models that guess a wrapper/index (e.g. results[0].from):
    # resolve the leaf field name against the first result object.
    if path:
        base = root[0] if isinstance(root, list) and root else root
        tokens = [s for s in _segments(path) if isinstance(s, str)]
        if isinstance(base, dict) and tokens:
            return base.get(tokens[-1])
    return None


def _resolve_value(value: object, outputs: Mapping[str, object]) -> object:
    if isinstance(value, str):
        whole = _REF.fullmatch(value.strip())
        if whole is not None:
            resolved = _lookup(outputs, whole.group(1))
            return value if resolved is None else resolved

        def _sub(match: re.Match[str]) -> str:
            found = _lookup(outputs, match.group(1))
            return match.group(0) if found is None else str(found)

        return _REF.sub(_sub, value)
    if isinstance(value, dict):
        return {k: _resolve_value(v, outputs) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_value(item, outputs) for item in value]
    return value


def resolve_references(
    arguments: Mapping[str, object], outputs: Mapping[str, object]
) -> dict[str, object]:
    """Return ``arguments`` with every ``{{step.field}}`` reference resolved."""
    return {key: _resolve_value(value, outputs) for key, value in arguments.items()}
