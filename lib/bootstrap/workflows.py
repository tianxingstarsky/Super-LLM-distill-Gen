"""Composition root: the only place that selects concrete workflow adapters."""
from pathlib import Path

from lib.application.workflow_service import WorkflowApplication
from lib.infrastructure.workflow_driver import FilesystemWorkflowDriver


def workflow_application(root: Path, output: Path) -> WorkflowApplication:
    return WorkflowApplication(FilesystemWorkflowDriver(root, output))


def human_augmentation_application(root: Path, output: Path):
    from lib.application.human_augmentation_service import HumanAugmentationApplication
    from lib.infrastructure.human_sessions import FilesystemHumanSessionDriver

    return HumanAugmentationApplication(FilesystemHumanSessionDriver(root, output))
