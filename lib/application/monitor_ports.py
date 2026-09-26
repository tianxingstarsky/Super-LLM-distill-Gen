"""Read boundary for bounded command-event history."""
from typing import Protocol


class EventHistoryPort(Protocol):
    def snapshot(self, kind: str | None, limit: int) -> dict: ...
