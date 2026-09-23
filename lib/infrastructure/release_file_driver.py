"""Filesystem adapter for conversation quality inputs and versioned releases."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from lib.exporters import export_minimind, export_samples
from lib.io_utils import atomic_json
from lib.domain.release_quality import sample_hash


class FilesystemReleaseDriver:
    def review_decisions(self, dataset_name: str) -> list[dict]:
        from lib import review_center

        return review_center.responses(dataset_name)

    def read_samples(self, path: Path) -> list[dict]:
        samples: list[dict] = []
        with Path(path).open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError("expected an object")
                except (ValueError, TypeError) as error:
                    raise ValueError(f"{path}:{number}: {error}") from error
                samples.append(row)
        return samples

    def write_release(self, samples: list[dict], fmt: str, parent: Path, quality: dict,
                      *, corpus_path: Path | None, dpo_path: Path | None,
                      tag: str | None, bulk: bool) -> tuple[Path, dict[str, int]]:
        version = tag or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        destination = Path(parent) / version
        destination.mkdir(parents=True, exist_ok=False)
        atomic_json(destination / "quality.json", quality)
        # A conversion failure keeps an explicit incomplete release on disk.
        atomic_json(destination / "manifest.json", {"status": "writing", "format": fmt})
        if fmt == "minimind":
            counts = export_minimind(samples, destination / "sft_t2t.jsonl", corpus_path, dpo_path)
        else:
            counts = export_samples(samples, fmt, destination / "sft.jsonl")
        files = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                 for path in destination.glob("*.jsonl")}
        atomic_json(destination / "manifest.json", {
            "status": "complete", "created_at": datetime.now(timezone.utc).isoformat(),
            "format": fmt, "bulk": bulk, "counts": counts, "sha256": files,
            "sample_hashes": [sample_hash(sample) for sample in samples],
        })
        return destination, counts
