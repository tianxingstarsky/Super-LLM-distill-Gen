"""Compatibility import; implementation lives in ``lib.contexts.documents.infrastructure.docling.pdf_text_visibility``."""
from importlib import import_module as _import_module
import sys as _sys
_sys.modules[__name__] = _import_module("lib.contexts.documents.infrastructure.docling.pdf_text_visibility")
