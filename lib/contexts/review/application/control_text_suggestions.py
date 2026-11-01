"""Read and resolve advisory matches without applying them to a task or draft."""
import re
from typing import Protocol


class ControlTextSuggestionPort(Protocol):
    def list_pending(self, node: str, limit: int) -> list[dict]: ...
    def get(self, identifier: str) -> dict: ...
    def resolve(self, identifier: str, status: str) -> None: ...


class ControlTextSuggestionApplication:
    def __init__(self, port: ControlTextSuggestionPort):
        self._port = port

    def list_pending(self, node, limit=30):
        if node not in {"sft", "multiturn", "cot"} or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("invalid_control_text_suggestion_query")
        return self._port.list_pending(node, limit)

    def get(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-f0-9]{64}", identifier):
            raise ValueError("invalid_control_text_suggestion_id")
        return self._port.get(identifier)

    def resolve(self, identifier, status):
        self.get(identifier)
        if status not in {"applied", "dismissed"}:
            raise ValueError("invalid_control_text_suggestion_status")
        self._port.resolve(identifier, status)
