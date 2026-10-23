"""The corpus use case needs extracted text, not a PDF or filesystem object."""
from typing import Protocol


class DocumentTextPort(Protocol):
    def read_text(self, source_path: str, *, source_processing_version: int = 2) -> str: ...
