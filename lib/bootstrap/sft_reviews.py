"""SFT review composition root."""
from pathlib import Path

from lib.application.sft_review_service import SftReviewApplication
from lib.infrastructure.sft_review_driver import FilesystemSftReviewDriver


def sft_review_application(output: Path) -> SftReviewApplication:
    return SftReviewApplication(FilesystemSftReviewDriver(Path(output)))
