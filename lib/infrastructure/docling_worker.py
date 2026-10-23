"""Historical worker script entrypoint; also preserves module import identity."""
from importlib import import_module as _import_module
import sys as _sys

if __name__ == "__main__":
    from pathlib import Path
    _sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    _import_module("lib.contexts.documents.infrastructure.docling.worker").main()
else:
    _sys.modules[__name__] = _import_module("lib.contexts.documents.infrastructure.docling.worker")
