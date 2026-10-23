"""Compatibility import; implementation lives in ``lib.contexts.models.application.service``."""
from importlib import import_module as _import_module
import sys as _sys

_sys.modules[__name__] = _import_module("lib.contexts.models.application.service")
