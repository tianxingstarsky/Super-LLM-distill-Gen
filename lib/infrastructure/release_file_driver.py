"""Filesystem adapter for conversation quality inputs and versioned releases."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Iterable, Iterator

from lib.exporters import export_minimind, export_samples
from lib.io_utils import atomic_json
from lib.domain.release_quality import sample_hash, update_source_digest


class ReplayableJSONLSamples:
    """Validate each JSONL row on every pass and reject a changed source."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._source_hash: str | None = None

    def __iter__(self) -> Iterator[dict]:
        source_hash = hashlib.sha256()
        with self.path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                source_hash.update(line.encode("utf-8"))
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    if not isinstance(row, dict):
                        raise ValueError("expected an object")
                except (ValueError, TypeError) as error:
                    raise ValueError(f"{self.path}:{number}: {error}") from error
                yield row
        fingerprint = source_hash.hexdigest()
        if self._source_hash is None:
            self._source_hash = fingerprint
        elif fingerprint != self._source_hash:
            raise ValueError("release_source_changed")


class FilesystemReleaseDriver:
    def raw_preview_samples(self, path: Path):
        from lib.infrastructure.sample_preview import RawSamplePreview
        return RawSamplePreview(path)

    def preview_samples(self, path: Path):
        from lib.infrastructure.sample_preview import SamplePreview
        return SamplePreview(path)

    def review_decisions(self, dataset_name: str) -> list[dict]:
        from lib import review_center

        return review_center.responses(dataset_name)

    def read_samples(self, path: Path) -> list[dict]:
        return list(self.replayable_samples(path))

    def replayable_samples(self, path: Path) -> ReplayableJSONLSamples:
        return ReplayableJSONLSamples(path)

    def write_release(self, samples: Iterable[dict], fmt: str, parent: Path, quality: dict,
                      *, expected_source_digest: str, corpus_path: Path | None, dpo_path: Path | None,
                      tag: str | None, bulk: bool) -> tuple[Path, dict[str, int]]:
        version = tag or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        destination = Path(parent) / version
        destination.mkdir(parents=True, exist_ok=False)
        atomic_json(destination / "quality.json", quality)
        # A conversion failure keeps an explicit incomplete release on disk.
        atomic_json(destination / "manifest.json", {"status": "writing", "format": fmt})
        sample_hashes = []
        source_digest = hashlib.sha256()

        def tracked_samples():
            for sample in samples:
                fingerprint = sample_hash(sample)
                sample_hashes.append(fingerprint)
                update_source_digest(source_digest, sample.get("id"), fingerprint)
                yield sample

        if fmt == "minimind":
            counts = export_minimind(tracked_samples(), destination / "sft_t2t.jsonl", corpus_path, dpo_path)
        else:
            counts = export_samples(tracked_samples(), fmt, destination / "sft.jsonl")
        if (len(sample_hashes) != quality["samples"]
                or source_digest.hexdigest() != expected_source_digest):
            raise ValueError("release_source_changed")
        files = {}
        for path in destination.glob("*.jsonl"):
            with path.open("rb") as handle:
                output_hash = hashlib.sha256()
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    output_hash.update(block)
                files[path.name] = output_hash.hexdigest()
        atomic_json(destination / "manifest.json", {
            "status": "complete", "created_at": datetime.now(timezone.utc).isoformat(),
            "format": fmt, "bulk": bulk, "counts": counts, "sha256": files,
            "sample_hashes": sample_hashes,
        })
        return destination, counts
