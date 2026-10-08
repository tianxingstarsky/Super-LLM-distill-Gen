"""Validate a complete upload batch before persisting any source documents."""
from __future__ import annotations

import re
from typing import Iterable

from lib.application.local_input_ports import LocalInputCachePort


ALLOWED_UPLOAD_SUFFIXES = frozenset({"pdf", "docx", "txt", "md", "json", "jsonl", "png", "jpg", "jpeg", "webp"})
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_UPLOAD_BATCH_BYTES = 200 * 1024 * 1024
_UNSAFE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f\ud800-\udfff]')
_WINDOWS_DEVICE = re.compile(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.I)


class LocalInputApplication:
    def __init__(self, port: LocalInputCachePort):
        self._port = port

    def store(self, uploads: Iterable[tuple[str, bytes]]) -> list[dict]:
        prepared: list[tuple[str, bytes]] = []
        total = 0
        for item in uploads:
            if not isinstance(item, (tuple, list)) or len(item) != 2:
                raise ValueError("invalid_upload_content")
            name, content = item
            if (not isinstance(name, str) or not name or len(name) > 240
                    or name in {".", ".."} or name.endswith((".", " "))
                    or _UNSAFE_NAME.search(name) or _WINDOWS_DEVICE.match(name)):
                raise ValueError("invalid_upload_name")
            # Keep the complete batch valid on common local filesystems too.
            if len(name.encode("utf-8")) > 255:
                raise ValueError("invalid_upload_name")
            if "." not in name or name.rsplit(".", 1)[-1].lower() not in ALLOWED_UPLOAD_SUFFIXES:
                raise ValueError("unsupported_upload_type")
            if not isinstance(content, bytes):
                raise ValueError("invalid_upload_content")
            if not content:
                raise ValueError("empty_upload")
            if len(content) > MAX_UPLOAD_BYTES:
                raise ValueError("upload_too_large")
            total += len(content)
            if total > MAX_UPLOAD_BATCH_BYTES:
                raise ValueError("upload_batch_too_large")
            prepared.append((name, content))
        if not prepared:
            raise ValueError("empty_upload_batch")
        return self._port.store(tuple(prepared))
