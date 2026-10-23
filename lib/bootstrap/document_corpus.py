"""Select the local reader for the document corpus use case."""
from lib.contexts.documents.api import PrepareDocumentCorpus
from lib.contexts.documents.infrastructure.native_reader import LocalDocumentTextReader


def document_corpus_application() -> PrepareDocumentCorpus:
    return PrepareDocumentCorpus(LocalDocumentTextReader())
