"""Typed DPO/ORPO adapter for the shared review store and streaming release."""
from __future__ import annotations
from pathlib import Path
from lib.infrastructure.training_review_driver import FilesystemTrainingReviewDriver


class FilesystemPreferenceReviewDriver(FilesystemTrainingReviewDriver):
    def __init__(self, output: Path, *, target: str = "dpo"):
        if target not in {"dpo", "orpo"}:
            raise ValueError("invalid_preference_review_target")
        super().__init__(output, target)
