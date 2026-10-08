"""On-demand document previews; they never create a run or call a model."""
from __future__ import annotations

from typing import Protocol


MAX_PREVIEW_BYTES = 2 * 1024 * 1024
MAX_PREVIEW_CHARS = 250_000
MAX_PREVIEW_PAGES = 40
MAX_DOCX_EXPANDED_BYTES = 4 * 1024 * 1024


class DocumentPreviewPort(Protocol):
    def describe(self, source: str, suffixes: frozenset[str]) -> dict: ...
    def preview(self, source: str, chunk_chars: int) -> dict: ...


class DocumentPreviewApplication:
    def __init__(self, port: DocumentPreviewPort):
        self._port = port

    def describe(self, source: str, suffixes: frozenset[str]) -> dict:
        return self._port.describe(source, suffixes)

    def signature(self, source: str, chunk_chars: int) -> tuple:
        self._validate_chunk_size(chunk_chars)
        row = self.describe(source, frozenset({".md", ".txt", ".pdf", ".docx"}))
        return (row["path"], row["version"], chunk_chars)

    def preview(self, source: str, chunk_chars: int) -> dict:
        self._validate_chunk_size(chunk_chars)
        return self._port.preview(source, chunk_chars)

    @staticmethod
    def _validate_chunk_size(chunk_chars: int) -> None:
        if type(chunk_chars) is not int or not 200 <= chunk_chars <= 20_000:
            raise ValueError("invalid_preview_chunk_size")
