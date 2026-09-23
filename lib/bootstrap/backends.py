"""Composition root for backend administration."""
from __future__ import annotations

from pathlib import Path

from lib.application.backend_service import BackendApplication
from lib.infrastructure.backend_config_driver import FilesystemBackendConfigDriver


def backend_application(root: Path) -> BackendApplication:
    return BackendApplication(FilesystemBackendConfigDriver(Path(root)))
