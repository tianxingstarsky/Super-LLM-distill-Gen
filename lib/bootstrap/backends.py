"""Composition root for backend administration."""
from __future__ import annotations

from pathlib import Path

from lib.contexts.models.api import BackendApplication
from lib.contexts.models.infrastructure.configuration import FilesystemBackendConfigDriver


def backend_application(root: Path) -> BackendApplication:
    return BackendApplication(FilesystemBackendConfigDriver(Path(root)))
