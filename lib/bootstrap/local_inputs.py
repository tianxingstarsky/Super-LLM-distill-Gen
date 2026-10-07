"""Connect the local source upload use case to its persistent cache."""
from __future__ import annotations

from pathlib import Path

from lib.application.local_input_service import LocalInputApplication
from lib.infrastructure.local_input_cache import FilesystemLocalInputCacheDriver


def local_input_application(root: Path | None = None) -> LocalInputApplication:
    return LocalInputApplication(FilesystemLocalInputCacheDriver(root))
