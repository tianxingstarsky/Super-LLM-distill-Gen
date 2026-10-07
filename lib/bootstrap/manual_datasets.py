"""Compose a persistent local manual dataset library."""
from pathlib import Path

from lib.application.manual_dataset_service import ManualDatasetApplication
from lib.infrastructure.manual_dataset_file import ManualDatasetFile


def manual_dataset_application(output: Path) -> ManualDatasetApplication:
    return ManualDatasetApplication(ManualDatasetFile(output))
