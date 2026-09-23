"""Workspace composition root."""
from pathlib import Path

from lib.application.workspace_service import WorkspaceApplication
from lib.infrastructure.workspace_driver import FilesystemWorkspaceDriver


def workspace_application(root: Path | None = None) -> WorkspaceApplication:
    return WorkspaceApplication(FilesystemWorkspaceDriver(root))
