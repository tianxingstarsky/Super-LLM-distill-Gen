"""Compatibility import; review owns the single pure wire contract."""
import sys as _sys
from importlib import import_module as _import_module
_sys.modules[__name__] = _import_module('lib.contexts.review.domain.wire_schema')
