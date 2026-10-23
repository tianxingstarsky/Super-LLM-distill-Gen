"""Compose the score/correct use case without changing runtime checkpoints."""
from lib.contexts.review.api import ReviewCandidate
from lib.contexts.review.infrastructure.automatic_adapter import CheckpointedReviewAdapter


def automatic_review(runtime):
    return ReviewCandidate(CheckpointedReviewAdapter(runtime), runtime.recipe)


def review_collections(runtime):
    from lib.contexts.review.api import ReviewGeneratedCollections
    from lib.contexts.review.infrastructure.automatic_adapter import StreamingReviewCollectionsAdapter
    return ReviewGeneratedCollections(StreamingReviewCollectionsAdapter(runtime), runtime.recipe)


def repair_branch(runtime):
    from lib.contexts.review.api import ExecuteRepairBranch
    from lib.contexts.review.infrastructure.automatic_adapter import AuthenticatedRepairAdapter
    return ExecuteRepairBranch(AuthenticatedRepairAdapter(runtime), runtime.recipe)
