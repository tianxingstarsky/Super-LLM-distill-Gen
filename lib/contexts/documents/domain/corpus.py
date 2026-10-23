"""A document identity and the result of preparing its local training corpus."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DocumentSource:
    location: str
    name: str
    extension: str


@dataclass(frozen=True)
class CorpusBuildResult:
    entries: list[dict[str, str]]
    source_location: str
    duplicate_chunks: int
    source_chars: int

    def as_record(self) -> dict:
        """Keep the existing CLI/file contract, including its duplicate count key."""
        return {"entries": self.entries, "stats": {
            "file": self.source_location, "chunks": len(self.entries) + self.duplicate_chunks,
            "kept": len(self.entries), "dups": self.duplicate_chunks, "chars": self.source_chars,
        }}
