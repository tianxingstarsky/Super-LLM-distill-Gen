"""Storage boundary for locally uploaded source documents."""
from __future__ import annotations

from typing import Protocol


class LocalInputCachePort(Protocol):
    def store(self, uploads: tuple[tuple[str, bytes], ...]) -> list[dict]: ...
