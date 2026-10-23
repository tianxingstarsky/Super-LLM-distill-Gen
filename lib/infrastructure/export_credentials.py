"""Block exact configured model credentials at training publication boundaries.

This is not a general-purpose secret detector or a rewriting filter. Originals
stay private and unchanged; an exact match blocks the entire publication.
"""
from __future__ import annotations

import os
from pathlib import Path
import zipfile

import yaml


class ExportCredentialGuard:
    def __init__(self, values=()):
        self._values = tuple(set(value for value in values if isinstance(value, str) and len(value) >= 12))
        self._bytes = tuple(value.encode("utf-8") for value in self._values)
        self._overlap = max(map(len, self._bytes), default=1) - 1

    @classmethod
    def from_root(cls, root=None):
        root = Path(root) if root is not None else Path(__file__).resolve().parents[2]
        backends = {}
        for name in ("backends.yaml", "backends.local.yaml"):
            path = root / "configs" / name
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.exists() else {}
            except (yaml.YAMLError, OSError, UnicodeError):
                raise ValueError("export_credential_config_invalid") from None
            if not isinstance(data, dict) or (data.get("backends") is not None and not isinstance(data["backends"], dict)):
                raise ValueError("export_credential_config_invalid")
            for key, value in (data.get("backends") or {}).items():
                if not isinstance(value, dict):
                    raise ValueError("export_credential_config_invalid")
                backends[key] = {**backends.get(key, {}), **value}
        values = [os.environ.get(name, "") for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY")]
        for backend in backends.values():
            values.append(backend.get("api_key"))
            reference = backend.get("api_key_env")
            if isinstance(reference, str):
                values.append(os.environ.get(reference, ""))
        return cls(values)

    def check_value(self, value):
        if not self._values:
            return
        pending = [value]
        while pending:
            item = pending.pop()
            if isinstance(item, str):
                if any(secret in item for secret in self._values):
                    raise ValueError("export_contains_configured_credential")
            elif isinstance(item, dict):
                pending.extend(item.keys())
                pending.extend(item.values())
            elif isinstance(item, (tuple, list)):
                pending.extend(item)

    def _check_stream(self, handle):
        tail = b""
        while block := handle.read(65536):
            value = tail + block
            if any(secret in value for secret in self._bytes):
                raise ValueError("export_contains_configured_credential")
            tail = value[-self._overlap:] if self._overlap else b""

    def check_file(self, path):
        if not self._values:
            return
        path = Path(path)
        self.check_value(path.name)
        if path.suffix.casefold() == ".zip":
            with zipfile.ZipFile(path) as archive:
                for item in archive.infolist():
                    self.check_value(item.filename)
                    with archive.open(item) as handle:
                        self._check_stream(handle)
        else:
            with path.open("rb") as handle:
                self._check_stream(handle)

    def check_folder(self, folder):
        if not self._values:
            return
        for path in Path(folder).iterdir():
            if path.is_file():
                self.check_file(path)
