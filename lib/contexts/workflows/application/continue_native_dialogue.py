"""Continue an authenticated first-answer repair through the ordinary dialogue loop."""
from __future__ import annotations

from copy import deepcopy
from .generate_dialogue import GenerateMultiturnDialogue
from ..domain.dialogue import dialogue_digest
from ..domain.native_dialogue_continuation import (
    native_dialogue_continuation_input, native_dialogue_progress_blocker,
)


class ContinueNativeDialogue:
    """The caller authenticates the frozen parent before constructing this use case."""

    def __init__(self, port, recipe, run_id):
        self.port, self.recipe, self.run_id = port, recipe, run_id

    def execute(self, item):
        original = item["record"]
        continuation = native_dialogue_continuation_input(original, item.get("instruction", ""),
            guidance_version=self.recipe.get("native_feedback_guidance_version", 1))
        reuse = self.recipe.get("reuse_initial_native_response") is True
        if reuse:
            progress_version = self.recipe.get("native_dialogue_progress_version")
            if (progress_version not in {1, 2}
                    or native_dialogue_progress_blocker(original, version=progress_version, diagnostic=True) is None):
                raise ValueError("native_dialogue_response_not_reusable")
            continuation["initial_native_response"] = deepcopy(original)
        unit = deepcopy(continuation["source"])
        rows = GenerateMultiturnDialogue(self.port, self.recipe).execute(unit, continuation=continuation)
        for row in rows:
            native = row.get("native_generation", {})
            feedback = native.get("review_guidance", native.get("tool_feedback"))
            feedback_unassessed = isinstance(feedback, dict) and feedback.get("semantic_status") == "not_assessed"
            row["id"] = dialogue_digest([self.run_id, "native_dialogue_continuation_v1",
                item["revision_context"]["content_sha256"]])
            row["revision_context"] = deepcopy(item["revision_context"])
            row["native_dialogue_continuation"] = {
                "version": 2 if reuse else 1, "repair_attempt_limit": 0 if reuse else 1,
                **({"initial_response_reused": True} if reuse else {}),
                "parent_run_id": item["revision_context"]["parent_run_id"],
                **{name: continuation[name] for name in ("parent_candidate_sha256", "source_sha256",
                    "director_contract_sha256", "initial_question_sha256")},
                "original": deepcopy(original),
                "generation_reused": ["source", "director_contract", "initial_question",
                    *(["initial_native_response"] if reuse else [])],
                "assessments": "not_assessed" if feedback_unassessed else "ordinary_turn_and_complete_dialogue_checks"}
        return rows
