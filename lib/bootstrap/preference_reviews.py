"""Preference-review composition root."""
from pathlib import Path

from lib.contexts.review.api import PreferenceReviewApplication
from lib.contexts.review.infrastructure.preference_review_driver import FilesystemPreferenceReviewDriver


def preference_review_application(output: Path, *, target: str = "dpo") -> PreferenceReviewApplication:
    return PreferenceReviewApplication(FilesystemPreferenceReviewDriver(Path(output), target=target))
