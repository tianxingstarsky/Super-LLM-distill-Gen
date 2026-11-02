"""Use sealed parent selections with the existing native runtime and checkpoints."""
from lib.infrastructure.workflow_store import read_json, digest
from lib.contexts.workflows.domain.planned_sft_reanswer import (planned_sft_prompt, valid_reanswer_receipt,
                                                            authenticated_teacher_text)


def parent_recipe(output, context):
    from lib.infrastructure.training_workflow import run_path
    value = read_json(run_path(output, context["parent_run_id"]) / "recipe.json")
    if digest(value) != context["recipe_sha256"]:
        raise ValueError("checkpoint_review_parent_changed")
    return value


def prepare_reanswer_parents(output, inputs):
    from lib.bootstrap.automatic_review import reviewed_checkpoint_input
    if not inputs or any(item.get("target") != "sft" or item.get("checkpoint_context", {}).get("version") != 2
                         for item in inputs):
        raise ValueError("sft_reanswer_sealed_native_parent_required")
    first = None
    for item in inputs:
        context = item["checkpoint_context"]
        actual = reviewed_checkpoint_input(output, context["parent_run_id"], "sft", context["candidate_id"])
        if actual != item:
            raise ValueError("checkpoint_review_parent_changed")
        parent = parent_recipe(output, context)
        if (parent.get("native_generation_version") != 1
                or parent.get("node_generation", {}).get("sft", {}).get("answer_mode") != "native_joint"
                or not parent.get("node_models", {}).get("sft", {}).get("generation")
                or not parent.get("endpoint_pins", {}).get("sft", {}).get("generation")
                or not item["record"].get("native_generation")
                or not context["evidence"].get("teacher_evidence")):
            raise ValueError("sft_reanswer_sealed_native_parent_required")
        planned_sft_prompt(item["record"], parent)
        authenticated_teacher_text(item["record"], context["evidence"], recipe=parent)
        if first is not None and any(parent.get(key) != first.get(key) for key in (
                "node_generation", "qa_director", "qa_learning_goal_mix_seed", "generation_prompts", "node_prompt_templates", "node_models", "endpoint_pins")):
            raise ValueError("sft_reanswer_incompatible_parents")
        first = parent
    return first


class PlannedSftRuntimeAdapter:
    def __init__(self, runtime, generation):
        self.runtime, self.generation = runtime, generation

    def parent_recipe(self, context):
        return parent_recipe(self.runtime.path.parent.parent, context)

    def generate(self, **kwargs):
        from ..application.native_generation import generate_native_turn
        return generate_native_turn(self.generation, self.runtime.recipe, **kwargs)


def reanswer_duplicate_allowed(runtime, row, duplicate):
    if (runtime.recipe.get("sft_reanswer_version") != 1 or not valid_reanswer_receipt(row)
            or duplicate.get("run_id") == runtime.state["id"]):
        return False
    receipt = row["sft_reanswer"]
    inputs = runtime.recipe.get("repair_inputs", [])
    selected = next((item for item in inputs if item.get("checkpoint_context") == receipt["parent_context"]), None)
    if selected is None:
        return False
    # Every allowed predecessor must be an actual sealed selection in this lineage.
    from lib.bootstrap.automatic_review import reviewed_checkpoint_input
    seen = set()
    while selected is not None:
        context = selected["checkpoint_context"]
        identity = (context["parent_run_id"], context["candidate_id"])
        if identity in seen:
            raise ValueError("sft_reanswer_parent_cycle")
        seen.add(identity)
        actual = reviewed_checkpoint_input(runtime.path.parent.parent, context["parent_run_id"], "sft", context["candidate_id"])
        if actual != selected:
            raise ValueError("checkpoint_review_parent_changed")
        if (duplicate.get("run_id") == context["parent_run_id"]
                and duplicate.get("id") == context["parent_run_id"] + ":" + context["candidate_id"]):
            return True
        ancestor = actual["record"].get("sft_reanswer", {}).get("parent_context")
        if not ancestor:
            return False
        selected = reviewed_checkpoint_input(runtime.path.parent.parent, ancestor["parent_run_id"], "sft", ancestor["candidate_id"])
    return False
