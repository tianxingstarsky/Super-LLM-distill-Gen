"""CPT corpus-review composition root."""
from pathlib import Path

from lib.application.corpus_review_service import CorpusReviewApplication
from lib.infrastructure.corpus_review_driver import FilesystemCorpusReviewDriver


def corpus_review_application(output: Path) -> CorpusReviewApplication:
    return CorpusReviewApplication(FilesystemCorpusReviewDriver(Path(output)))
