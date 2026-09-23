"""Storage boundary for reading samples and writing immutable trainer releases."""
from __future__ import annotations

from pathlib import Path
from typing import Protocol


class ReleasePort(Protocol):
    def read_samples(self, path: Path) -> list[dict]: ...

    def review_decisions(self, dataset_name: str) -> list[dict]: ...

    def write_release(self, samples: list[dict], fmt: str, parent: Path, quality: dict,
                      *, corpus_path: Path | None, dpo_path: Path | None,
                      tag: str | None, bulk: bool) -> tuple[Path, dict[str, int]]: ...
