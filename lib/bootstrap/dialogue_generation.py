"""Compose a dialogue use case with the existing checkpointed request runtime."""
from lib.contexts.workflows.application.generate_dialogue import GenerateMultiturnDialogue
from lib.contexts.workflows.infrastructure.dialogue_adapter import CheckpointedDialogueAdapter


def dialogue_generation(runtime) -> GenerateMultiturnDialogue:
    """Build multi-turn orchestration with preserved actor history and checkpoints."""
    return GenerateMultiturnDialogue(CheckpointedDialogueAdapter(runtime), runtime.recipe)


def continue_native_dialogue(runtime, item):
    """An authenticated explicit repair keeps the author's node and request runtime."""
    from lib.contexts.workflows.api import ContinueNativeDialogue
    if not getattr(runtime, "_authenticated_repair_branch", False):
        raise ValueError("native_dialogue_continuation_parent_not_authenticated")
    return ContinueNativeDialogue(CheckpointedDialogueAdapter(runtime, model_stage="multiturn"),
        runtime.recipe, runtime.state["id"]).execute(item)
