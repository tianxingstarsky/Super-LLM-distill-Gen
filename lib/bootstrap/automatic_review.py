"""Compose the score/correct use case without changing runtime checkpoints."""
from lib.contexts.review.api import ReviewCandidate
from lib.contexts.review.infrastructure.automatic_adapter import CheckpointedReviewAdapter


def automatic_review(runtime):
    from lib.contexts.review.api import ReviewPreferenceView
    return ReviewPreferenceView(ReviewCandidate(CheckpointedReviewAdapter(runtime), runtime.recipe), runtime.recipe)


def review_collections(runtime):
    from lib.contexts.review.api import ReviewGeneratedCollections
    from lib.contexts.review.infrastructure.automatic_adapter import StreamingReviewCollectionsAdapter
    return ReviewGeneratedCollections(StreamingReviewCollectionsAdapter(runtime), runtime.recipe)


def repair_branch(runtime):
    from lib.contexts.review.api import ExecuteRepairBranch
    from lib.contexts.review.infrastructure.automatic_adapter import AuthenticatedRepairAdapter
    return ExecuteRepairBranch(AuthenticatedRepairAdapter(runtime), runtime.recipe)


def claim_windows(runtime, **options):
    from lib.contexts.review.api import ReviewClaimWindows
    from lib.contexts.review.infrastructure.claim_window_adapter import CheckpointedClaimWindowAdapter
    return ReviewClaimWindows(CheckpointedClaimWindowAdapter(runtime, **options))


def training_format_lineage(runtime):
    from lib.contexts.review.api import AuthorizeTrainingFormatView
    from lib.contexts.review.infrastructure.format_lineage import SealedFormatLineageAdapter
    return AuthorizeTrainingFormatView(SealedFormatLineageAdapter(runtime))


def preference_assessment(runtime):
    from lib.contexts.review.api import AssessPreferencePair
    from lib.contexts.review.infrastructure.preference_assessment import CheckpointedPreferenceAssessmentAdapter
    return AssessPreferencePair(CheckpointedPreferenceAssessmentAdapter(runtime), runtime.recipe)
