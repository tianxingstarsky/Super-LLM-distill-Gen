"""Creation form persistence through an inward-facing port."""
from typing import Protocol
from lib.domain.creation_draft import validate_creation_draft


class CreationDraftPort(Protocol):
    def load(self) -> dict: ...
    def update(self, changes: dict) -> None: ...


class CreationDraftApplication:
    def __init__(self, port: CreationDraftPort):
        self._port = port

    def load(self) -> dict:
        return validate_creation_draft(self._port.load())

    def update(self, changes: dict) -> None:
        self._port.update(validate_creation_draft(changes))
