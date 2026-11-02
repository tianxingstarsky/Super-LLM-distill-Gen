"""Compose fixed-task SFT regeneration and sealed-parent preparation."""
from types import SimpleNamespace


def planned_sft_regenerator(runtime):
    from lib.contexts.workflows.application.regenerate_planned_sft import RegeneratePlannedSft
    from lib.contexts.workflows.infrastructure.planned_sft_reanswer import PlannedSftRuntimeAdapter
    from lib.contexts.workflows.infrastructure.generation_adapter import CheckpointedGenerationAdapter
    return RegeneratePlannedSft(PlannedSftRuntimeAdapter(runtime, CheckpointedGenerationAdapter(runtime)),
                                runtime.recipe, runtime.state["id"])


def prepare_reanswer_parents(output, inputs):
    from lib.contexts.workflows.infrastructure.planned_sft_reanswer import prepare_reanswer_parents as prepare
    return prepare(output, inputs)


def reanswer_duplicate_allowed(runtime, row, duplicate):
    from lib.contexts.workflows.infrastructure.planned_sft_reanswer import reanswer_duplicate_allowed as allowed
    return allowed(runtime, row, duplicate)


def create_sft_reanswer(root, output, parent_run_id, candidate_ids, agent_experience=None, *, task_instructions=None):
    from lib.bootstrap.automatic_review import reviewed_checkpoint_input
    from lib.bootstrap.workflows import workflow_application
    from lib.contexts.workflows.application.regenerate_planned_sft import CreatePlannedSftReanswer
    return CreatePlannedSftReanswer(SimpleNamespace(
        select=lambda parent, candidate: reviewed_checkpoint_input(output, parent, "sft", candidate),
        parent_configuration=lambda selected: prepare_reanswer_parents(output, selected),
        create=workflow_application(root, output).create_run)).execute(parent_run_id, candidate_ids, agent_experience,
            **({"task_instructions": task_instructions} if task_instructions is not None else {}))
