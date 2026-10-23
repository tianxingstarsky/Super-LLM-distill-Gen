"""CPT corpus-review composition root."""
from pathlib import Path

from lib.contexts.review.api import CorpusReviewApplication
from lib.contexts.review.infrastructure.corpus_review_driver import FilesystemCorpusReviewDriver


def corpus_review_application(output: Path) -> CorpusReviewApplication:
    return CorpusReviewApplication(FilesystemCorpusReviewDriver(Path(output)))
