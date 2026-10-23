"""Compatibility import for the single production runtime implementation."""
import sys as _sys
from importlib import import_module as _import_module
_sys.modules[__name__] = _import_module("lib.contexts.workflows.infrastructure.production_runtime")
