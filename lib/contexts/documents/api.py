"""Public document-preparation use case; adapters are assembled in bootstrap."""
from .application.prepare_corpus import PrepareDocumentCorpus
from .application.ports import DocumentTextPort
from .domain.corpus import CorpusBuildResult, DocumentSource
