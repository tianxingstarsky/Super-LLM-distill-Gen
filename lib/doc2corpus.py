"""Compatibility facade for local document-to-corpus commands.

Pure text rules belong to contexts.documents.domain; the application coordinates
preparation, and the local reader/writer owns I/O. Historical imports remain valid.
"""
from __future__ import annotations

from pathlib import Path

from lib.contexts.documents.api import DocumentSource
from lib.contexts.documents.contracts import (
    normalize_plain_text as clean_text,
    normalized_chunk_id as chunk_hash,
    split_text_into_chunks as chunk_text,
)
from lib.contexts.documents.infrastructure.native_reader import (
    SUPPORTED_EXTS, _decode_text, import_text,
)
from lib.contexts.documents.infrastructure.corpus_writer import write_corpus_jsonl


def doc_to_corpus(path: str | Path, target_chars: int = 2000, overlap: int = 0,
                  manifest: set[str] | None = None) -> dict:
    """Retain the original result shape and shared cross-document identity set."""
    from lib.bootstrap.document_corpus import document_corpus_application

    source_path = Path(path)
    source = DocumentSource(location=str(path), name=source_path.name,
                            extension=source_path.suffix.lower())
    result = document_corpus_application().execute(
        source, target_chars=target_chars, overlap=overlap, known_chunk_ids=manifest)
    return result.as_record()
