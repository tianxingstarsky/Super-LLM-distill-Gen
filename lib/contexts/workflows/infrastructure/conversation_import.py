"""Local adapter subprocess with staged, hash-checked import receipts."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from filelock import FileLock

from ..domain.conversation_import import MAX_IMPORT_BYTES, import_digest


class ExternalConversationImporter:
    def __init__(self, root: Path, command: tuple[str, ...], source_store, *, timeout: float = 120,
                 cache_directory: Path | None = None, suffixes: tuple[str, ...] = ()):
        self._cache = Path(cache_directory or (Path(root).resolve() / "data" / "conversation-imports"))
        self._command = tuple(command)
        self._source_store = source_store
        self._timeout = timeout
        self._suffixes = suffixes
        self._fingerprints = {}

    def _adapter_identity(self) -> str:
        if not self._command or not self._command[0] or not Path(self._command[0]).is_file():
            raise ValueError("conversation_import_runtime_unavailable")
        if len(self._command) < 2 or not Path(self._command[1]).is_file():
            raise ValueError("conversation_import_adapter_unavailable")
        hashes = []
        artifacts = list(self._command)
        script_directory = Path(self._command[1]).parent
        for directory in (script_directory, *list(script_directory.parents)[:2]):
            artifacts.extend(str(directory / filename) for filename in
                             ("package.json", "package-lock.json", "compatibility.json", "node_modules/.package-lock.json"))
        for argument in artifacts:
            path = Path(argument)
            if path.is_file():
                info = path.stat()
                key = (str(path), info.st_size, info.st_mtime_ns)
                if key not in self._fingerprints:
                    with path.open("rb") as stream:
                        self._fingerprints[key] = hashlib.file_digest(stream, "sha256").hexdigest()
                hashes.append(self._fingerprints[key])
        return import_digest(json.dumps([self._command, hashes], ensure_ascii=False).encode("utf-8"))

    def _directory(self, identity: str) -> Path:
        if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{64}", identity):
            raise ValueError("conversation_import_invalid_id")
        directory = self._cache / identity
        from lib.infrastructure.local_input_cache import _assert_unlinked
        _assert_unlinked(directory)
        return directory

    @staticmethod
    def _read(path: Path, limit=MAX_IMPORT_BYTES) -> bytes:
        from lib.infrastructure.local_input_cache import _assert_unlinked
        _assert_unlinked(path)
        if not path.is_file() or path.stat().st_size > limit:
            raise ValueError("conversation_import_invalid_output_size")
        return path.read_bytes()

    def convert(self, name: str, content: bytes) -> tuple[bytes, dict]:
        if self._suffixes and not name.lower().endswith(self._suffixes):
            raise ValueError("conversation_import_unsupported_file")
        adapter_identity = self._adapter_identity()
        identity = import_digest((import_digest(content) + adapter_identity).encode("ascii"))
        cached = self._directory(identity)
        if (cached / "ready.json").is_file():
            metadata, normalized, report = self.load(identity)
            if metadata["source_sha256"] != import_digest(content):
                raise ValueError("conversation_import_receipt_mismatch")
            return normalized, report
        if not self._command or not Path(self._command[0]).is_file():
            raise ValueError("conversation_import_runtime_unavailable")
        with tempfile.TemporaryDirectory(prefix="dataforge-conversation-") as temporary:
            directory = Path(temporary)
            input_directory = directory / "input"
            input_directory.mkdir()
            source, output, receipt = input_directory / name, directory / "normalized.jsonl", directory / "report.json"
            source.write_bytes(content)
            try:
                completed = subprocess.run([*self._command, "--input", str(source), "--output", str(output),
                    "--report", str(receipt)], shell=False, capture_output=True, timeout=self._timeout,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except subprocess.TimeoutExpired:
                raise ValueError("conversation_import_timeout") from None
            except OSError:
                raise ValueError("conversation_import_runtime_unavailable") from None
            try:
                intact_source = import_digest(source.read_bytes()) == import_digest(content)
            except OSError:
                intact_source = False
            if not intact_source:
                raise ValueError("conversation_import_source_modified")
            try:
                report = json.loads(self._read(receipt, 1024 * 1024))
            except (ValueError, OSError):
                raise ValueError("conversation_import_invalid_report") from None
            if not isinstance(report, dict):
                raise ValueError("conversation_import_invalid_report")
            if completed.returncode != 0 or report.get("status") != "ready":
                error = report.get("error")
                code = error.get("code") if isinstance(error, dict) else "conversation_import_rejected"
                if not isinstance(code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,100}", code):
                    code = "conversation_import_rejected"
                raise ValueError(code)
            return self._read(output), {**report, "conversion_adapter_sha256": adapter_identity}

    def stage(self, name: str, source_sha256: str, normalized: bytes, report: dict) -> str:
        adapter_identity = report.get("conversion_adapter_sha256")
        if not isinstance(adapter_identity, str) or not re.fullmatch(r"[a-f0-9]{64}", adapter_identity):
            raise ValueError("conversation_import_receipt_mismatch")
        identity = import_digest((source_sha256 + adapter_identity).encode("ascii"))
        directory = self._directory(identity)
        self._cache.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self._cache / (identity + ".lock")), timeout=10):
            self._directory(identity)
            directory.mkdir(exist_ok=True)
            for filename, content in (
                ("normalized.jsonl", normalized),
                ("report.json", json.dumps(report, ensure_ascii=False).encode("utf-8")),
                ("ready.json", json.dumps({"source_name": name, "source_sha256": source_sha256,
                    "adapter_sha256": adapter_identity}, ensure_ascii=False).encode("utf-8")),
            ):
                target = directory / filename
                if target.exists():
                    if filename != "ready.json" and self._read(target) != content:
                        raise ValueError("conversation_import_cache_mismatch")
                    continue
                with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
        return identity

    def load(self, import_id: str) -> tuple[dict, bytes, dict]:
        directory = self._directory(import_id)
        try:
            metadata = json.loads(self._read(directory / "ready.json", 1024 * 1024))
            report = json.loads(self._read(directory / "report.json", 1024 * 1024))
            normalized = self._read(directory / "normalized.jsonl")
        except (ValueError, OSError):
            raise ValueError("conversation_import_cache_invalid") from None
        if (not isinstance(metadata, dict) or not isinstance(metadata.get("source_sha256"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", metadata["source_sha256"])
                or not isinstance(metadata.get("adapter_sha256"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", metadata["adapter_sha256"])
                or import_digest((metadata["source_sha256"] + metadata["adapter_sha256"]).encode("ascii")) != import_id):
            raise ValueError("conversation_import_cache_invalid")
        from ..domain.conversation_import import validate_import_name
        validate_import_name(metadata.get("source_name"))
        if not isinstance(report, dict) or report.get("conversion_adapter_sha256") != metadata["adapter_sha256"]:
            raise ValueError("conversation_import_cache_invalid")
        return metadata, normalized, report

    def store_source(self, name: str, content: bytes) -> list[dict]:
        return self._source_store.store([(name, content)])
