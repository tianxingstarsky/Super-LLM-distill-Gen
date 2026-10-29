"""Bind optional imported-turn completion to the existing request checkpoints."""
from lib.contexts.workflows.application.prepare_conversation_reasoning import PrepareConversationReasoning
from lib.contexts.workflows.infrastructure.generation_adapter import CheckpointedGenerationAdapter


def conversation_reasoning_preparer(runtime) -> PrepareConversationReasoning:
    """Build the input-node use case without invoking a model or changing sources."""
    return PrepareConversationReasoning(CheckpointedGenerationAdapter(runtime),
                                        runtime.recipe.get("conversation_reasoning"))
