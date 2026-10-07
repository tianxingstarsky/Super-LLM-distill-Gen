"""Creation form persistence through an inward-facing port."""
from typing import Protocol
from lib.domain.creation_draft import validate_creation_draft


class CreationDraftPort(Protocol):
    def load(self) -> dict: ...
    def update(self, changes: dict) -> None: ...
    def replace(self, values: dict) -> None: ...
    def save_snapshot(self, name: str | None = None, *, values: dict | None = None) -> str: ...
    def snapshots(self, limit: int = 50, offset: int = 0) -> list[dict]: ...
    def restore_snapshot(self, identifier: str) -> dict: ...


class CreationDraftApplication:
    def __init__(self, port: CreationDraftPort):
        self._port = port

    def load(self) -> dict:
        return validate_creation_draft(self._port.load())

    def update(self, changes: dict) -> None:
        self._port.update(validate_creation_draft(changes))

    def replace(self, values: dict) -> None:
        self._port.replace(validate_creation_draft(values))

    def save_snapshot(self, name: str | None = None, *, values: dict | None = None) -> str:
        if name is not None and (not isinstance(name, str) or len(name) > 100):
            raise ValueError('invalid_creation_draft')
        if values is None:
            return self._port.save_snapshot(name)
        return self._port.save_snapshot(name, values=validate_creation_draft(values))

    def snapshots(self, limit: int = 50, offset: int = 0) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 500 or type(offset) is not int or offset < 0:
            raise ValueError('invalid_creation_draft_page')
        return self._port.snapshots(limit, offset)

    def restore_snapshot(self, identifier: str) -> dict:
        return validate_creation_draft(self._port.restore_snapshot(identifier))
