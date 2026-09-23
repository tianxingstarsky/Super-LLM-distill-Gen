"""Existing-folder storage adapter."""
from pathlib import Path

from lib import workspace as legacy


class FilesystemWorkspaceDriver:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root is not None else None

    @property
    def default_id(self):
        return legacy.DEFAULT

    def add_folder(self, path):
        return legacy.add_folder(path, root=self.root)

    def list_workspaces(self):
        return legacy.list_all(root=self.root)

    def resolve(self, workspace_id=None):
        return legacy.resolve(workspace_id, root=self.root)

    def folder(self, workspace_id=None):
        return legacy.folder(workspace_id, root=self.root)

    def output(self, workspace_id=None):
        return legacy.out(workspace_id, root=self.root)

    def label(self, workspace_id):
        return legacy.label(workspace_id, root=self.root)

    def dataset_name(self, workspace_id):
        return legacy.dataset_name(workspace_id, root=self.root)

    def source_files(self, workspace_id, limit=500, suffixes=None):
        return legacy.source_files(workspace_id, limit=limit, root=self.root, suffixes=suffixes)
