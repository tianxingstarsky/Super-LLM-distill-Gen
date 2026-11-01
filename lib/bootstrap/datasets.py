"""Compose dataset use cases without constructing a generation engine."""
from pathlib import Path

from lib.contexts.datasets.api import DatasetApplication
from lib.contexts.datasets.infrastructure.driver import FilesystemDatasetDriver


def dataset_application(root: Path, output: Path) -> DatasetApplication:
    return DatasetApplication(FilesystemDatasetDriver(root, output))


def qwen35_training_encoder(tokenizer, *, max_length=None, effort_mapping=None):
    """Compose optional training preprocessing without loading model weights."""
    from lib.contexts.datasets.infrastructure.qwen35_training import Qwen35TrainingEncoder

    return Qwen35TrainingEncoder(tokenizer, max_length=max_length, effort_mapping=effort_mapping)
