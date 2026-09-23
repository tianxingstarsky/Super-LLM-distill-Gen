"""Quality-report and versioned-export use cases shared by CLI and console."""
from __future__ import annotations

from pathlib import Path
import re

from lib.application.release_ports import ReleasePort
from lib.domain.release_quality import report


class ReleaseApplication:
    def __init__(self, port: ReleasePort):
        self._port = port

    def read_samples(self, path: Path) -> list[dict]:
        return self._port.read_samples(Path(path))

    @staticmethod
    def quality_report(samples: list[dict], decisions=()) -> dict:
        return report(samples, decisions)

    def quality_report_for_dataset(self, samples: list[dict], dataset_name: str) -> dict:
        return self.quality_report(samples, self._port.review_decisions(dataset_name))

    def export_release(self, samples: list[dict], fmt: str, parent: Path, decisions=(),
                       corpus_path: Path | None = None, dpo_path: Path | None = None,
                       tag: str | None = None, bulk: bool = False) -> tuple[Path, dict[str, int]]:
        quality = self.quality_report(samples, decisions)
        if bulk and not quality["ready_for_bulk"]:
            raise ValueError("Quality blocked: " + ", ".join(quality["block_reasons"]))
        if tag and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tag):
            raise ValueError("tag must contain 1-64 letters, digits, underscores or hyphens")
        return self._port.write_release(samples, fmt, Path(parent), quality,
                                        corpus_path=Path(corpus_path) if corpus_path else None,
                                        dpo_path=Path(dpo_path) if dpo_path else None,
                                        tag=tag, bulk=bulk)

    def export_release_for_dataset(self, samples: list[dict], fmt: str, parent: Path,
                                   dataset_name: str, **options) -> tuple[Path, dict[str, int]]:
        return self.export_release(samples, fmt, parent,
                                   self._port.review_decisions(dataset_name), **options)
