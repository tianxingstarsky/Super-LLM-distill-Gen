"""Pinned, local-only evaluation references for CPT overlap checks.

Evaluation records are deliberately separate from training inputs and are
never sent to a model or copied into the published training artifacts.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

from lib.domain.corpus_decontamination import EvaluationOverlapIndex, EvaluationReference


EVALUATION_EXTENSIONS = frozenset({".json", ".jsonl"})
MAX_EVALUATION_FILE_BYTES = 5 * 1024 * 1024
MAX_EVALUATION_TOTAL_BYTES = 25 * 1024 * 1024
MAX_EVALUATION_FILES = 20
_SNAPSHOT_FILE = re.compile(r"[0-9]{4}\.(?:json|jsonl)\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")


def _evaluation_rows(data: bytes, suffix: str, name: str, *, source_key: str) -> list[EvaluationReference]:
    try:
        content = data.decode("utf-8")
        if suffix == ".jsonl":
            rows = [json.loads(line) for line in content.splitlines() if line.strip()]
        elif suffix == ".json":
            document = json.loads(content)
            rows = document if isinstance(document, list) else [document]
        else:
            raise ValueError("unsupported_evaluation_reference")
        if not rows:
            raise ValueError("empty_evaluation_reference")
        references = []
        for index, row in enumerate(rows, 1):
            if not isinstance(row, dict) or set(row) != {"text"} or not isinstance(row["text"], str):
                raise ValueError("invalid_evaluation_record")
            identifier = hashlib.sha256(f"{source_key}:{index}:{row['text']}".encode("utf-8")).hexdigest()
            references.append(EvaluationReference(identifier, name, index, row["text"]))
        return references
    except (UnicodeError, json.JSONDecodeError, TypeError, RecursionError) as error:
        raise ValueError("invalid_evaluation_record") from error


def snapshot_evaluation_sources(run_path: Path, sources: list[Path],
                                source_names: dict[str, str] | None = None) -> list[dict]:
    """Validate and copy evaluation files before a recipe is persisted."""
    if len(sources) > MAX_EVALUATION_FILES:
        raise ValueError("evaluation_reference_limit_exceeded")
    entries: list[tuple[str, bytes, str, list[EvaluationReference]]] = []
    total_bytes = 0
    for index, source in enumerate(sources):
        path = Path(source).resolve(strict=True)
        suffix = path.suffix.lower()
        if not path.is_file() or suffix not in EVALUATION_EXTENSIONS:
            raise ValueError("unsupported_evaluation_reference")
        if path.stat().st_size > MAX_EVALUATION_FILE_BYTES:
            raise ValueError("evaluation_reference_limit_exceeded")
        data = path.read_bytes()
        total_bytes += len(data)
        if len(data) > MAX_EVALUATION_FILE_BYTES or total_bytes > MAX_EVALUATION_TOTAL_BYTES:
            raise ValueError("evaluation_reference_limit_exceeded")
        name = (source_names or {}).get(str(path), path.name)
        if not isinstance(name, str) or not name or len(name) > 255:
            raise ValueError("invalid_evaluation_reference_name")
        rows = _evaluation_rows(data, suffix, name, source_key=f"{index:04d}:{name}")
        entries.append((name, data, suffix, rows))
    if entries:
        EvaluationOverlapIndex([row for _, _, _, rows in entries for row in rows])
    if not entries:
        return []
    directory = Path(run_path) / "evaluations"
    directory.mkdir(exist_ok=False)
    manifests = []
    for index, (name, data, suffix, rows) in enumerate(entries):
        file_name = f"{index:04d}{suffix}"
        (directory / file_name).write_bytes(data)
        manifests.append({"name": name, "file": file_name,
                          "sha256": hashlib.sha256(data).hexdigest(),
                          "bytes": len(data), "records": len(rows)})
    return manifests


def load_evaluation_index(run_path: Path, manifests: list[dict]) -> EvaluationOverlapIndex | None:
    """Recheck immutable snapshots, then build a local read-only overlap index."""
    if not manifests:
        return None
    if not isinstance(manifests, list) or len(manifests) > MAX_EVALUATION_FILES:
        raise ValueError("evaluation_reference_integrity_error")
    references = []
    directory = Path(run_path) / "evaluations"
    try:
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError("invalid_evaluation_directory")
        expected_names = set()
        total_bytes = 0
        for index, manifest in enumerate(manifests):
            if (not isinstance(manifest, dict) or set(manifest) != {"name", "file", "sha256", "bytes", "records"}
                    or not isinstance(manifest["name"], str) or not manifest["name"]
                    or manifest["file"] != f"{index:04d}{Path(manifest['file']).suffix.lower()}"
                    or not _SNAPSHOT_FILE.fullmatch(manifest["file"])
                    or not isinstance(manifest["sha256"], str) or not _HASH.fullmatch(manifest["sha256"])
                    or type(manifest["bytes"]) is not int or manifest["bytes"] > MAX_EVALUATION_FILE_BYTES
                    or type(manifest["records"]) is not int or manifest["records"] < 1):
                raise ValueError("invalid_evaluation_manifest")
            expected_names.add(manifest["file"])
            item = directory / manifest["file"]
            if item.is_symlink() or not item.is_file():
                raise ValueError("missing_evaluation_snapshot")
            if item.stat().st_size > MAX_EVALUATION_FILE_BYTES:
                raise ValueError("evaluation_snapshot_mismatch")
            data = item.read_bytes()
            total_bytes += len(data)
            if (len(data) != manifest["bytes"]
                    or total_bytes > MAX_EVALUATION_TOTAL_BYTES
                    or hashlib.sha256(data).hexdigest() != manifest["sha256"]):
                raise ValueError("evaluation_snapshot_mismatch")
            rows = _evaluation_rows(data, item.suffix.lower(), manifest["name"],
                                    source_key=f"{index:04d}:{manifest['name']}")
            if len(rows) != manifest["records"]:
                raise ValueError("evaluation_record_count_mismatch")
            references.extend(rows)
        if {path.name for path in directory.iterdir()} != expected_names:
            raise ValueError("evaluation_snapshot_inventory_mismatch")
        return EvaluationOverlapIndex(references)
    except (OSError, KeyError, TypeError, AttributeError, ValueError) as error:
        raise ValueError("evaluation_reference_integrity_error") from error
