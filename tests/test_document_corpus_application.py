"""Pure corpus preparation can use text supplied by any reader adapter."""
from lib.contexts.documents.api import DocumentSource, PrepareDocumentCorpus


class MemoryDocumentReader:
    def __init__(self, documents):
        self.documents = documents
        self.calls = []

    def read_text(self, source_path, *, source_processing_version=2):
        self.calls.append(source_path)
        return self.documents[source_path]


def test_memory_reader_preserves_structure_and_shared_duplicate_manifest():
    text = "# Details\n\n12.7\n\n```python\n    value = 12.7\n```\n"
    reader = MemoryDocumentReader({"first": text, "second": text})
    prepare = PrepareDocumentCorpus(reader)
    known = set()
    first = prepare.execute(DocumentSource("first", "manual.md", ".md"), target_chars=30, known_chunk_ids=known)
    second = prepare.execute(DocumentSource("second", "copy.md", ".md"), target_chars=30, known_chunk_ids=known)
    assert "".join(row["text"] for row in first.entries) == text.strip("\n")
    assert second.entries == [] and second.duplicate_chunks == len(first.entries)
    assert reader.calls == ["first", "second"]
    assert first.as_record()["stats"]["kept"] == len(first.entries)


def test_latex_identities_preserve_semantic_whitespace():
    reader = MemoryDocumentReader({"one": r"\begin{verbatim}a b\end{verbatim}",
                                   "two": r"\begin{verbatim}ab\end{verbatim}"})
    prepare = PrepareDocumentCorpus(reader)
    known = set()
    first = prepare.execute(DocumentSource("one", "one.tex", ".tex"), known_chunk_ids=known)
    second = prepare.execute(DocumentSource("two", "two.tex", ".tex"), known_chunk_ids=known)
    assert len(first.entries) == len(second.entries) == 1
    assert second.duplicate_chunks == 0
