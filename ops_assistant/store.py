"""Storage seam.

The service depends on a :class:`WorkflowStore` interface, not on a concrete
dict. Stage 1 wires the in-memory implementation; Stage 2 adds a Postgres one
behind the same interface, so "state survives restart" is a swap, not a rewrite.

The contract is deliberately narrow — ``create`` once, ``get`` to load, ``save``
to persist the whole aggregate after a unit of work. ``save`` is where optimistic
locking lives for a real database; in memory it is a no-op because the record is
mutated in place.
"""

from __future__ import annotations

from typing import Protocol

from ops_assistant.state import WorkflowRecord


class WorkflowStore(Protocol):
    def create(self, workflow: WorkflowRecord) -> None: ...

    def get(self, workflow_id: str) -> WorkflowRecord | None: ...

    def save(self, workflow: WorkflowRecord) -> None: ...


class InMemoryWorkflowStore:
    """Dict-backed store. The whole aggregate lives by reference, so ``save`` only
    needs to bump the version the way a database would."""

    def __init__(self) -> None:
        self._workflows: dict[str, WorkflowRecord] = {}

    def create(self, workflow: WorkflowRecord) -> None:
        self._workflows[workflow.id] = workflow

    def get(self, workflow_id: str) -> WorkflowRecord | None:
        return self._workflows.get(workflow_id)

    def save(self, workflow: WorkflowRecord) -> None:
        workflow.version += 1
        self._workflows[workflow.id] = workflow
