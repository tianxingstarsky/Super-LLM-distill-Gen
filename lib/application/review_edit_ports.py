"""Outbound model boundary for a human reviewer's field-level suggestion."""
from __future__ import annotations

from typing import Any, Protocol


class ReviewSuggestionPort(Protocol):
    def complete_json(self, messages: list[dict[str, str]], *, temperature: float) -> Any:
        """Return a parsed JSON value without mutating the review draft."""
        ...
