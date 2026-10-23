"""Prepare corpus records without importing a document reader or storage adapter."""
from __future__ import annotations

import hashlib

from .ports import DocumentTextPort
from ..domain.corpus import CorpusBuildResult, DocumentSource
from ..domain.latex import LATEX_EXTENSIONS, chunk_latex
from ..domain.text_chunks import normalize_plain_text, normalized_chunk_id, split_text_into_chunks


class PrepareDocumentCorpus:
    def __init__(self, reader: DocumentTextPort):
        self._reader = reader

    def execute(self, source: DocumentSource, *, target_chars: int = 2000, overlap: int = 0,
                known_chunk_ids: set[str] | None = None) -> CorpusBuildResult:
        # The caller may deliberately share this set across documents.
        known_chunk_ids = known_chunk_ids if known_chunk_ids is not None else set()
        is_latex = source.extension in LATEX_EXTENSIONS
        extracted_text = self._reader.read_text(source.location)
        text = extracted_text.strip() if is_latex else normalize_plain_text(extracted_text)
        if not text:
            return CorpusBuildResult([], source.location, 0, 0)
        if is_latex:
            if overlap:
                raise ValueError("latex_overlap_not_supported")
            chunks = chunk_latex(text, target_chars)
        else:
            chunks = split_text_into_chunks(text, target_chars, overlap)
        entries = []
        duplicate_chunks = 0
        for chunk_index, chunk in enumerate(chunks):
            # TeX verbatim/code whitespace is meaningful. Keep historical
            # normalized identities for every other format.
            chunk_id = (hashlib.sha256(chunk.encode("utf-8")).hexdigest()[:16]
                        if is_latex else normalized_chunk_id(chunk))
            if chunk_id in known_chunk_ids:
                duplicate_chunks += 1
                continue
            known_chunk_ids.add(chunk_id)
            entries.append({"text": chunk, "source": source.name,
                            "chunk_id": f"{source.name}#{chunk_index}"})
        return CorpusBuildResult(entries, source.location, duplicate_chunks, len(text))
