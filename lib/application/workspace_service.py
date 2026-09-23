"""Workspace selection and source-catalog use cases."""
from pathlib import Path

from lib.application.workspace_ports import WorkspaceDriver


class WorkspaceApplication:
    def __init__(self, driver: WorkspaceDriver):
        self._driver = driver

    @property
    def default_id(self) -> str:
        return self._driver.default_id

    def open_folder(self, path: str | Path) -> str:
        return self._driver.add_folder(path)

    def available_workspaces(self) -> list[str]:
        return self._driver.list_workspaces()

    def resolve(self, workspace_id: str | None = None) -> str:
        return self._driver.resolve(workspace_id)

    def folder(self, workspace_id: str | None = None) -> Path:
        return self._driver.folder(workspace_id)

    def output(self, workspace_id: str | None = None) -> Path:
        return self._driver.output(workspace_id)

    def label(self, workspace_id: str) -> str:
        return self._driver.label(workspace_id)

    def dataset_name(self, workspace_id: str) -> str:
        return self._driver.dataset_name(workspace_id)

    def source_files(self, workspace_id: str, *, limit: int = 500, suffixes=None) -> list[Path]:
        return self._driver.source_files(workspace_id, max(1, min(int(limit), 5000)), suffixes)
