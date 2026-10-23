"""Compatibility import; the canonical implementation owns all behavior."""
import sys as _sys
from importlib import import_module as _import_module
_sys.modules[__name__] = _import_module('lib.contexts.review.infrastructure.automatic_runtime')
