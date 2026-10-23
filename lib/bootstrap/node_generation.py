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
    return GenerateCot(CheckpointedGenerationAdapter(runtime), runtime.recipe)


def reasoning_trimmer(runtime):
    return TrimReasoning(StreamingReasoningAdapter(runtime), runtime.recipe)
