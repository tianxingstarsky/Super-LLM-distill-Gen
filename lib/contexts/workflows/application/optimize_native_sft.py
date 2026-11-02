"""Generate one same-task native replacement from a saved review observation."""
from copy import deepcopy

from lib.contexts.review.contracts import (
    review_evolution_step_snapshot, review_evolution_system_instruction,
)
from lib.contexts.models.contracts import (
    BudgetExceeded, ModelOutputLimitError, ModelQueueTimeout, ModelRequestError, ModelStreamError,
)
from ..domain.dialogue import dialogue_digest
from ..domain.generation import directed_sft_instruction
from ..domain.native_sft_optimization import native_sft_optimization_input
from .native_generation import generate_native_turn


class OptimizeNativeSft:
    def __init__(self, port, recipe, run_id):
        self.port, self.recipe, self.run_id = port, recipe, run_id

    def execute(self, original, evidence, observation, memory_receipt):
        self.port.authorize_native_sft_optimization(original)
        prepared = native_sft_optimization_input(original, self.recipe, evidence)
        snapshot = review_evolution_step_snapshot(observation, self.recipe)
        identity = dialogue_digest([self.run_id, "native_sft_review_evolution_v1", original["id"],
            dialogue_digest(observation), dialogue_digest(memory_receipt)])

        def generate_once():
            try:
                row = generate_native_turn(self.port, self.recipe, stage="sft", unit=prepared["unit"],
                    messages=prepared["messages"], style=deepcopy(original.get("generation_style")),
                    source_text=prepared["source_text"], source_scope=prepared["source_scope"],
                    identity=identity, instruction=(directed_sft_instruction(self.recipe, original["qa_contract"])
                        + "\n" + review_evolution_system_instruction(observation)))
            except (BudgetExceeded, ModelOutputLimitError, ModelQueueTimeout, ModelRequestError, ModelStreamError) as error:
                return {"record": None, "derived_snapshot": snapshot,
                    "issue": "native_sft_optimization_request_failed", "error_type": type(error).__name__}
            row.update({key: deepcopy(original[key]) for key in (
                "source_context", "qa_contract", "family_id", "source_name", "location",
                "source_location", "control_text_config", "control_text_node") if key in original})
            current = native_sft_optimization_input(row, self.recipe, evidence)
            return {"record": row, "task_identity": {"original": prepared["task_sha256"],
                "current": current["task_sha256"]}, "derived_snapshot": snapshot}

        return self.port.checkpoint(["native_sft_optimization_attempt_v1", identity], generate_once)
