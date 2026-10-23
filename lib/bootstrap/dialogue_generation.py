"""Compose a dialogue use case with the existing checkpointed request runtime."""
from lib.contexts.workflows.application.generate_dialogue import GenerateMultiturnDialogue
from lib.contexts.workflows.infrastructure.dialogue_adapter import CheckpointedDialogueAdapter


def dialogue_generation(runtime):
    return GenerateMultiturnDialogue(CheckpointedDialogueAdapter(runtime), runtime.recipe)
