"""Authenticate an older publication before recognizing its sibling format."""
from __future__ import annotations

from typing import Protocol
from ..domain.training_lineage import TRAINING_TASK_VIEWS, same_training_task_view


class FormatLineagePort(Protocol):
    def authenticate_current(self, context: dict) -> None: ...
    def publication_owner(self, duplicate: dict) -> tuple[str, dict] | None: ...
    def authenticate_owner(self, run_id: str, record: dict) -> None: ...


class AuthorizeTrainingFormatView:
    def __init__(self, port: FormatLineagePort):
        self._port = port

    def execute(self, row, target, duplicate):
        context = row.get("revision_context") or {}
        if target not in TRAINING_TASK_VIEWS or not context or not duplicate:
            return False
        self._port.authenticate_current(context)
        owner = self._port.publication_owner(duplicate)
        if owner is None:
            return False
        owner_target, record = owner
        if not same_training_task_view(row, target, record, owner_target):
            return False
        self._port.authenticate_owner(duplicate["run_id"], record)
        return True
