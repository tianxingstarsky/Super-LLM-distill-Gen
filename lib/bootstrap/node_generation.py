"""Build generation nodes against one run's frozen recipe and checkpoints.

This module selects adapters; node algorithms belong to workflow application
use cases. Construction does not generate samples or select a different model.
"""
from lib.contexts.workflows.application.generate_sft import GenerateSft
from lib.contexts.workflows.application.generate_directed_sft import GenerateDirectedSft
from lib.contexts.workflows.application.generate_cot import GenerateCot
from lib.contexts.workflows.application.trim_reasoning import TrimReasoning
from lib.contexts.workflows.infrastructure.generation_adapter import (
    CheckpointedGenerationAdapter, StreamingReasoningAdapter,
)


def sft_generator(runtime) -> GenerateSft:
    """Build SFT generation from prepared sources or imported conversations."""
    return GenerateSft(CheckpointedGenerationAdapter(runtime), runtime.recipe)


def directed_sft_generator(runtime) -> GenerateDirectedSft:
    """Build SFT answers for questions assigned by the dialogue director."""
    return GenerateDirectedSft(CheckpointedGenerationAdapter(runtime), runtime.recipe)


def cot_generator(runtime) -> GenerateCot:
    """Build CoT generation with source lookup and optional numeric verification."""
    from .workflow_requests import workflow_request_adapter
    from lib.infrastructure import training_workflow
    from lib.contexts.workflows.infrastructure.generation_source_adapter import GenerationSourceAdapter
    source_lookup = GenerationSourceAdapter(runtime, workflow_request_adapter(runtime, vars(training_workflow)))
    from lib.contexts.review.api import VerifyReferenceAnswer
    from lib.contexts.review.infrastructure.math_reference_adapter import MathVerifyReferenceAdapter
    return GenerateCot(CheckpointedGenerationAdapter(runtime, source_lookup), runtime.recipe,
                       reference_verifier=VerifyReferenceAnswer(MathVerifyReferenceAdapter()))


def authored_submission_repairer(runtime):
    """Build explicit same-author recovery and local edits for a saved submission.

    The adapter authenticates the original author and request prefix. Opening
    a page or constructing this use case does not automatically retry a request.
    """
    from .workflow_requests import workflow_request_adapter
    from lib.infrastructure import training_workflow
    from lib.contexts.workflows.infrastructure.generation_source_adapter import GenerationSourceAdapter
    from lib.contexts.workflows.application.repair_authored_submission import RepairAuthoredSubmission
    from lib.contexts.workflows.infrastructure.authored_edit_adapter import AuthoredEditAdapter
    source_lookup = GenerationSourceAdapter(runtime, workflow_request_adapter(runtime, vars(training_workflow)))
    return RepairAuthoredSubmission(
        CheckpointedGenerationAdapter(runtime, source_lookup), AuthoredEditAdapter(runtime)
    )


def reasoning_trimmer(runtime) -> TrimReasoning:
    """Build configured reasoning cleanup and streaming training-record projection."""
    return TrimReasoning(StreamingReasoningAdapter(runtime), runtime.recipe)


def preference_generator(runtime):
    """Build preference pairs, reusing an authenticated failed answer when suitable."""
    from lib.contexts.workflows.application.generate_preference import GeneratePreferencePair
    return GeneratePreferencePair(CheckpointedGenerationAdapter(runtime), runtime.recipe)
