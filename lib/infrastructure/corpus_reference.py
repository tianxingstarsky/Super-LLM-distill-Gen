"""Verified, pinned CPT release references for cross-run exact overlap checks.

An on-disk release is an integrity-checked reference, not proof of copyright,
reviewer identity, factual accuracy, or freedom from benchmark contamination.
Only published CPT releases in the same output workspace are considered.
"""
from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
from pathlib import Path
import re

from lib.domain.corpus_quality import CorpusNearDuplicateIndex, comparison_text
from lib.domain.corpus_review import validate_corpus_row


_RUN_ID = re.compile(r"[a-f0-9]{32}\Z")
_VERSION = re.compile(r"cpt-v(\d{4,})\Z")


def _sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _release_path(output: Path, run_id: str, version: int) -> Path:
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id):
        raise ValueError("cpt_release_integrity_error")
    if type(version) is not int or version < 1:
        raise ValueError("cpt_release_integrity_error")
    return output / "workflows" / run_id / "releases" / f"cpt-v{version:04d}"


def _verified_rows(folder: Path, run_id: str, version: int, *,
                   on_text: Callable[[int, str], None] | None = None) -> tuple[dict, int, str]:
    try:
        if (folder.is_symlink() or folder.parent.is_symlink()
                or folder.parent.parent.is_symlink() or not folder.is_dir()):
            raise ValueError("invalid_release_directory")
        manifest_path = folder / "manifest.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise ValueError("missing_release_manifest")
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        hashes = manifest.get("sha256")
        if (manifest.get("status") != "human_reviewed" or manifest.get("target") != "cpt"
                or manifest.get("run_id") != run_id or manifest.get("version") != version
                or not isinstance(hashes, dict) or "cpt.jsonl" not in hashes):
            raise ValueError("invalid_release_manifest")
        inventory = {item.name for item in folder.iterdir()}
        if inventory != set(hashes) | {"manifest.json"}:
            raise ValueError("release_inventory_mismatch")
        row_count = 0
        for name, expected in hashes.items():
            item = folder / name
            if (not isinstance(name, str) or Path(name).name != name
                    or not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected)
                    or item.is_symlink() or not item.is_file()):
                raise ValueError("release_file_hash_mismatch")
            if name == "cpt.jsonl":
                digest = hashlib.sha256()
                with item.open("rb") as handle:
                    for line in handle:
                        digest.update(line)
                        if not line.strip():
                            raise ValueError("blank_corpus_record")
                        text = validate_corpus_row(json.loads(line.decode("utf-8")))["text"]
                        row_count += 1
                        if on_text is not None:
                            on_text(row_count, text)
                if digest.hexdigest() != expected:
                    raise ValueError("release_file_hash_mismatch")
            elif _sha256(item) != expected:
                raise ValueError("release_file_hash_mismatch")
        if not row_count or manifest.get("counts", {}).get("approved") != row_count:
            raise ValueError("release_count_mismatch")
        return manifest, row_count, hashlib.sha256(manifest_bytes).hexdigest()
    except (OSError, UnicodeError, ValueError, TypeError, KeyError, AttributeError) as error:
        raise ValueError("cpt_release_integrity_error") from error


def snapshot_released_corpus(output: Path) -> list[dict]:
    """Pin all currently published CPT releases, excluding in-progress staging."""
    output = Path(output)
    references = []
    for run in sorted((output / "workflows").glob("*")):
        if not _RUN_ID.fullmatch(run.name):
            continue
        if run.is_symlink():
            raise ValueError("cpt_release_integrity_error")
        for folder in sorted((run / "releases").glob("cpt-v*")):
            match = _VERSION.fullmatch(folder.name)
            if not match:
                continue
            version = int(match.group(1))
            if folder.name != f"cpt-v{version:04d}":
                raise ValueError("cpt_release_integrity_error")
            manifest, _, manifest_sha256 = _verified_rows(folder, run.name, version)
            references.append({"run_id": run.name, "version": version,
                               "manifest_sha256": manifest_sha256,
                               "corpus_sha256": manifest["sha256"]["cpt.jsonl"]})
    return references


def _released_indexes(output: Path, references: list[dict], *, include_near: bool
                      ) -> tuple[dict[str, dict], CorpusNearDuplicateIndex | None, int]:
    """Recheck pinned releases once before their text becomes a dedup reference."""
    index: dict[str, dict] = {}
    near_index = CorpusNearDuplicateIndex() if include_near else None
    total = 0
    for reference in references:
        try:
            run_id, version = reference["run_id"], reference["version"]
            folder = _release_path(Path(output), run_id, version)
            def add(line: int, text: str) -> None:
                fingerprint = hashlib.sha256(comparison_text(text).encode("utf-8")).hexdigest()
                match = {"run_id": run_id, "version": version, "line": line,
                         "corpus_sha256": reference["corpus_sha256"]}
                index.setdefault(fingerprint, match)
                if near_index is not None:
                    release_id = f"{run_id}/cpt-v{version:04d}/{line}"
                    near_index.add_reference(text, {"id": release_id, "source_name": release_id,
                                                    "reference_release": match})
            manifest, row_count, manifest_sha256 = _verified_rows(folder, run_id, version, on_text=add)
            if (reference["manifest_sha256"] != manifest_sha256
                    or reference["corpus_sha256"] != manifest["sha256"]["cpt.jsonl"]):
                raise ValueError("release_snapshot_changed")
        except (KeyError, TypeError, OSError, ValueError) as error:
            raise ValueError("cpt_release_integrity_error") from error
        total += row_count
    return index, near_index, total


def released_exact_index(output: Path, references: list[dict]) -> tuple[dict[str, dict], int]:
    """Build a compact exact index for callers that do not need near matching."""
    exact, _, total = _released_indexes(output, references, include_near=False)
    return exact, total


def released_corpus_index(output: Path, references: list[dict]
                          ) -> tuple[dict[str, dict], CorpusNearDuplicateIndex, int]:
    """Return exact and conservative near indexes of verified pinned releases."""
    exact, near, total = _released_indexes(output, references, include_near=True)
    assert near is not None
    return exact, near, total


def exact_release_match(index: dict[str, dict], text: str) -> dict | None:
    fingerprint = hashlib.sha256(comparison_text(text).encode("utf-8")).hexdigest()
    return index.get(fingerprint)
