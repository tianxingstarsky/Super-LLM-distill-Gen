"""Compose SFT/CoT use cases with the existing checkpointed model runtime."""
from lib.contexts.workflows.application.generate_sft import GenerateSft
from lib.contexts.workflows.application.generate_directed_sft import GenerateDirectedSft
from lib.contexts.workflows.application.generate_cot import GenerateCot
from lib.contexts.workflows.application.trim_reasoning import TrimReasoning
from lib.contexts.workflows.infrastructure.generation_adapter import (
    CheckpointedGenerationAdapter, StreamingReasoningAdapter,
)


def sft_generator(runtime):
    return GenerateSft(CheckpointedGenerationAdapter(runtime), runtime.recipe)


def directed_sft_generator(runtime):
    return GenerateDirectedSft(CheckpointedGenerationAdapter(runtime), runtime.recipe)


def cot_generator(runtime):
    from .workflow_requests import workflow_request_adapter
    from lib.infrastructure import training_workflow
    from lib.contexts.workflows.infrastructure.generation_source_adapter import GenerationSourceAdapter
    lookup = GenerationSourceAdapter(runtime, workflow_request_adapter(runtime, vars(training_workflow)))
    from lib.contexts.review.api import VerifyReferenceAnswer
    from lib.contexts.review.infrastructure.math_reference_adapter import MathVerifyReferenceAdapter
    return GenerateCot(CheckpointedGenerationAdapter(runtime, lookup), runtime.recipe,
                       reference_verifier=VerifyReferenceAnswer(MathVerifyReferenceAdapter()))


def authored_submission_repairer(runtime):
    """Explicit, bounded recovery; never runs merely because a page is reopened."""
    from .workflow_requests import workflow_request_adapter
    from lib.infrastructure import training_workflow
    from lib.contexts.workflows.infrastructure.generation_source_adapter import GenerationSourceAdapter
    from lib.contexts.workflows.application.repair_authored_submission import RepairAuthoredSubmission
    from lib.contexts.workflows.infrastructure.authored_edit_adapter import AuthoredEditAdapter
    lookup = GenerationSourceAdapter(runtime, workflow_request_adapter(runtime, vars(training_workflow)))
    return RepairAuthoredSubmission(CheckpointedGenerationAdapter(runtime, lookup), AuthoredEditAdapter(runtime))


def reasoning_trimmer(runtime):
    return TrimReasoning(StreamingReasoningAdapter(runtime), runtime.recipe)


def preference_generator(runtime):
    from lib.contexts.workflows.application.generate_preference import GeneratePreferencePair
    return GeneratePreferencePair(CheckpointedGenerationAdapter(runtime), runtime.recipe)
