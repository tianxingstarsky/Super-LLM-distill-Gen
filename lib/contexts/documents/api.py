"""Public corpus preparation and its source/result contracts.

PrepareDocumentCorpus reads through DocumentTextPort, preserves supported
document structure and builds corpus records. DocumentSource identifies an
input; CorpusBuildResult contains prepared records, source location and counts.
Parser choice belongs to bootstrap and infrastructure, not this API module.
"""
from .application.prepare_corpus import PrepareDocumentCorpus
from .application.ports import DocumentTextPort
from .domain.corpus import CorpusBuildResult, DocumentSource
