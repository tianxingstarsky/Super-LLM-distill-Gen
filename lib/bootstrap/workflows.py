"""Composition root: the only place that selects concrete workflow adapters."""
from pathlib import Path

from lib.application.workflow_service import WorkflowApplication
from lib.bootstrap.datasets import dataset_application
from lib.bootstrap.agent_review import agent_review_application
from lib.bootstrap.workflow_reports import workflow_report_application
from lib.contexts.workflows.infrastructure.driver import FilesystemWorkflowRunDriver


def workflow_application(root: Path, output: Path) -> WorkflowApplication:
    return WorkflowApplication(FilesystemWorkflowRunDriver(root, output),
                               datasets=dataset_application(root, output),
                               review=agent_review_application(root, output),
                               reports=workflow_report_application(root, output))


def human_augmentation_application(root: Path, output: Path):
    from lib.application.human_augmentation_service import HumanAugmentationApplication
    from lib.infrastructure.human_sessions import FilesystemHumanSessionDriver

    return HumanAugmentationApplication(FilesystemHumanSessionDriver(root, output))
