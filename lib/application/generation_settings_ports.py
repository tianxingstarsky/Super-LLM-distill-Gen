"""Storage boundary for the three fixed generation-settings documents."""
from __future__ import annotations

from typing import Protocol


class GenerationSettingsPort(Protocol):
    def read(self, category: str) -> str: ...

    def compare_and_swap(self, category: str, expected: str, replacement: str) -> None: ...
