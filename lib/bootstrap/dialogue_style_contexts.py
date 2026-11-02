"""Freeze the small source-free style catalog; construction never calls a model."""
from lib.contexts.workflows.domain.dialogue_style_context import freeze_catalog
from lib.prompts.workflow_dialogue_contexts import CONTEXT_CATALOG


def dialogue_style_context_catalog():
    return freeze_catalog(CONTEXT_CATALOG)
