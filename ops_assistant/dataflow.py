"""Inter-step data-flow.

A plan step can reference an earlier step's output with ``{{step_id.field}}``
(dotted paths, array indices, and whole-output ``{{step_id}}`` are allowed). Before
a step runs, the executor resolves these against the outputs of the steps that
already succeeded — so a draft goes to the *actual* sender found by the search
step, not a placeholder.

Resolution is deliberately lenient about the *shape* a model guesses: when the
exact path misses, it falls back to the referenced leaf field on the first result
object, so ``{{s1.results[0].from}}`` still finds ``from`` (see ``_lookup``). The
trade-off — pinned by tests — is that a mistaken path whose leaf name happens to
exist elsewhere resolves to that value instead of failing. A reference that
resolves to nothing is left literal, never blanked. Two things bound the risk: the
policy engine requires every reference to name a declared, already-succeeded
dependency, and a human approves the *resolved* arguments before any side-effect.
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


def referenced_steps(arguments: Mapping[str, object]) -> set[str]:
    """The set of step ids referenced by ``{{step_id.field}}`` anywhere in ``arguments``.

    The policy engine uses this to require that a step declares (in ``depends_on``)
    every step it references — so a reference resolves against an *already-succeeded
    dependency*, never an unrelated later step. That equality is what lets an
    approval preview show the value that will actually execute.
    """
    found: set[str] = set()
    _collect_refs(arguments, found)
    return found


def _collect_refs(value: object, found: set[str]) -> None:
    if isinstance(value, str):
        for match in _REF.finditer(value):
            step, _ = _parse_ref(match.group(1))
            if step:
                found.add(step)
    elif isinstance(value, Mapping):
        for item in value.values():
            _collect_refs(item, found)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _collect_refs(item, found)
