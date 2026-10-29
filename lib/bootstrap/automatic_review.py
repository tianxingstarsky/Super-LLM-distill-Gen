"""Build automatic review, bounded repair and preference checks for a run.

Factories return use cases; calling a factory does not score or repair a
candidate. Each use case keeps the runtime's frozen recipe and checkpoints.
"""
from lib.contexts.review.api import ReviewCandidate
from lib.contexts.review.infrastructure.automatic_adapter import CheckpointedReviewAdapter


def automatic_review(runtime):
    """Build a candidate reviewer with shared review across preference views."""
    from lib.contexts.review.api import ReviewPreferenceView
    candidate_reviewer = ReviewCandidate(CheckpointedReviewAdapter(runtime), runtime.recipe)
    return ReviewPreferenceView(candidate_reviewer, runtime.recipe)


def review_collections(runtime):
    """Build streaming collection review with CoT/SFT lineage alignment."""
    from lib.contexts.review.api import ReviewGeneratedCollections
    from lib.contexts.review.infrastructure.automatic_adapter import StreamingReviewCollectionsAdapter
    return ReviewGeneratedCollections(StreamingReviewCollectionsAdapter(runtime), runtime.recipe)


def repair_branch(runtime):
    """Build explicit rework; execution authenticates all selected parent versions."""
    from lib.contexts.review.api import ExecuteRepairBranch
    from lib.contexts.review.infrastructure.automatic_adapter import AuthenticatedRepairAdapter
    return ExecuteRepairBranch(AuthenticatedRepairAdapter(runtime), runtime.recipe)


def claim_windows(runtime, **options):
    """Build bounded claim review using the supplied request-adapter options."""
    from lib.contexts.review.api import ReviewClaimWindows
    from lib.contexts.review.infrastructure.claim_window_adapter import CheckpointedClaimWindowAdapter
    return ReviewClaimWindows(CheckpointedClaimWindowAdapter(runtime, **options))


def training_format_lineage(runtime):
    """Build format-view authorization, not another content quality review."""
    from lib.contexts.review.api import AuthorizeTrainingFormatView
    from lib.contexts.review.infrastructure.format_lineage import SealedFormatLineageAdapter
    return AuthorizeTrainingFormatView(SealedFormatLineageAdapter(runtime))


def preference_assessment(runtime):
    """Build response-quality and chosen/rejected contrast assessment."""
    from lib.contexts.review.api import AssessPreferencePair
    from lib.contexts.review.infrastructure.preference_assessment import CheckpointedPreferenceAssessmentAdapter
    return AssessPreferencePair(CheckpointedPreferenceAssessmentAdapter(runtime), runtime.recipe)
