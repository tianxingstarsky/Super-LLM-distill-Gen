"""Compose dataset use cases without constructing a generation engine."""
from pathlib import Path

from lib.contexts.datasets.api import DatasetApplication
from lib.contexts.datasets.infrastructure.driver import FilesystemDatasetDriver


def dataset_application(root: Path, output: Path) -> DatasetApplication:
    return DatasetApplication(FilesystemDatasetDriver(root, output))
