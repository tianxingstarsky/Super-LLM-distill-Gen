"""Compose the same checklist use case for a revised CoT candidate."""
from lib.contexts.workflows.api import CheckReasoning
from lib.contexts.workflows.infrastructure.checklist_adapter import CheckpointedChecklistAdapter


def reasoning_checklist(runtime, config):
    return CheckReasoning(CheckpointedChecklistAdapter(runtime), config)
