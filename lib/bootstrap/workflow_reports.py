"""Composition root for the portable workflow report."""
from pathlib import Path

from lib.application.workflow_report_service import WorkflowReportApplication
from lib.infrastructure.workflow_report import FilesystemWorkflowReportDriver


def workflow_report_application(root: Path, output: Path) -> WorkflowReportApplication:
    return WorkflowReportApplication(FilesystemWorkflowReportDriver(root, output))
