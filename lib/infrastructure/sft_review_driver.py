"""Typed SFT adapter for the shared review store and streaming release."""
from __future__ import annotations
from pathlib import Path
from lib.infrastructure.training_review_driver import FilesystemTrainingReviewDriver


class FilesystemSftReviewDriver(FilesystemTrainingReviewDriver):
    def __init__(self, output: Path):
        super().__init__(output, "sft")
