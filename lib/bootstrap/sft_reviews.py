"""SFT review composition root."""
from pathlib import Path

from lib.contexts.review.api import SftReviewApplication
from lib.contexts.review.infrastructure.sft_review_driver import FilesystemSftReviewDriver


def sft_review_application(output: Path) -> SftReviewApplication:
    return SftReviewApplication(FilesystemSftReviewDriver(Path(output)))
