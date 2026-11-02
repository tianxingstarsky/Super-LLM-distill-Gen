"""Review the first native answer once and optionally generate one replacement."""
from copy import deepcopy

from lib.domain.workflow_quality import accepted, conversation_issue, text_issue
from ..domain.review_evolution import (
    finalize_native_sft_review_evolution, native_sft_review_evolution_release_valid,
)


class ReviewNativeSft:
    def __init__(self, port, recipe, config):
        self.port, self.recipe, self.config = port, recipe, config

    def execute(self, original):
        try:
            payload = self.port.payload("sft", original)
        except (KeyError, TypeError, ValueError):
            payload = None
        if "native_sft_review_evolution" in original:
            if not native_sft_review_evolution_release_valid(original, payload, self.config):
                raise ValueError("native_sft_review_evolution_receipt_changed")
            return [{**deepcopy(original), "_review_target": "sft"}]

        messages = original.get("messages")
        structural = conversation_issue(messages)
        if not structural:
            final = messages[-1]
            structural = ("native_sft_review_evolution_native_response_required"
                if final.get("role") != "assistant" or not original.get("native_generation")
                or text_issue(final.get("content")) or text_issue(final.get("reasoning_content")) else None)
        if structural or payload is None:
            return self._finalize(original, payload, {"status": "not_called", "model_called": False},
                {"status": "not_called"}, action="invalid", issue=structural or "missing_native_sft_payload")

        evidence = self.port.evidence(original)
        observation = self.port.review_once("sft", original, evidence, "")
        memory = (self.port.record_review_memory("sft", original, observation)
            if observation.get("status") == "observed" else {"status": "not_called"})
        check = observation.get("verdict")
        if observation.get("status") != "observed" or not isinstance(check, dict):
            return self._finalize(original, payload, observation, memory, action="not_optimized",
                issue="native_sft_review_evolution_observation_unavailable")
        if accepted(check) and min(check["scores"].values()) / 5 >= self.config["score_threshold"]:
            return self._finalize(original, payload, observation, memory,
                action="accepted" if original.get("status") == "eligible" else "invalid",
                issue=original.get("reason") if original.get("status") != "eligible" else None)
        if check.get("keep") is not False or not observation.get("task_notes"):
            return self._finalize(original, payload, observation, memory, action="not_optimized",
                issue="native_sft_review_evolution_no_actionable_rejection")

        try:
            optimized = self.port.optimize_native_sft(original, evidence, observation, memory)
        except ValueError as error:
            if str(error) not in {"native_sft_optimization_author_session_required", "native_sft_optimization_author_changed"}:
                raise
            return self._finalize(original, payload, observation, memory, action="not_optimized", issue=str(error))
        revised = optimized.get("record")
        if revised is None:
            return self._finalize(original, payload, observation, memory, action="not_optimized",
                derived_snapshot=optimized.get("derived_snapshot"),
                issue=optimized.get("issue", "native_sft_optimization_request_failed"))
        return self._finalize(original, payload, observation, memory, action="revised", revised=revised,
            revised_payload=self.port.payload("sft", revised), task_identity=optimized["task_identity"],
            derived_snapshot=optimized["derived_snapshot"], issue=revised.get("reason"))

    def _finalize(self, original, payload, observation, memory, **options):
        row = finalize_native_sft_review_evolution(original, observation, memory,
            config=self.config, original_payload=payload, **options)
        if row.get("status") == "quarantined" and not row.get("reason"):
            row["reason"] = options.get("issue") or "native_sft_review_evolution_not_assessed"
        row["_review_target"] = "sft"
        return [row]
