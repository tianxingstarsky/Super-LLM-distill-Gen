"""Composition root for review queues and persistent batch rework."""
from pathlib import Path

from lib.application.review_rework_service import ReviewReworkApplication
from lib.bootstrap.workflows import human_augmentation_application
from lib.infrastructure.review_rework_driver import FilesystemReviewReworkDriver


def review_rework_application(root: Path, output: Path, *, human_application=None):
    human = human_application or human_augmentation_application(Path(root), Path(output))
    return ReviewReworkApplication(FilesystemReviewReworkDriver(Path(output)), human)
