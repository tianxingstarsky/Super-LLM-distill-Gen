"""Use the existing full SFT claim reviewer without pair labels or siblings."""
from __future__ import annotations

from copy import deepcopy
from ..domain.candidate_content import candidate_digest


class CheckpointedPreferenceAssessmentAdapter:
    def __init__(self, runtime):
        self._runtime = runtime

    def assess_response(self, projected, evidence, *, response_sha256, instruction):
        # Body identity, rather than a predetermined chosen/rejected role or
        # repair attempt, reuses an unchanged response's actual checkpoint.
        key = [projected["id"], "anonymous_preference_response_v1", response_sha256,
               candidate_digest(self._runtime._review_payload("sft", projected)),
               candidate_digest(evidence), candidate_digest(instruction)]
        verdict = self._runtime._review_score("sft", projected, evidence, key, instruction)
        audit = projected.get("claim_review")
        return {"verdict": deepcopy(verdict), "claim_review": deepcopy(audit),
            "reasoning_assessment_reference": deepcopy(projected.get("reasoning_assessment_reference")),
            "learner_context_check": deepcopy(projected.get("learner_context_check")),
            "condition_pending": bool(audit and any(
                assessment["review"].get("condition_guard", {}).get("needs_review")
                for assessment in audit.get("assessments", [])))}

    def assess_contrast(self, row, evidence, *, instruction):
        from ..domain.preference_candidates import validate_contrast_assessment
        payload = self._runtime._review_payload("dpo", row)
        role = "jev" if self._runtime.recipe.get("package_review", {}).get("enabled") else "generation"
        key = [row["id"], "preference_contrast_v1", candidate_digest(payload), candidate_digest(evidence),
               candidate_digest(instruction), candidate_digest(self._runtime.recipe["preference_design"])]
        request = {"contrastive_design": self._runtime.recipe["preference_design"], "candidate": payload,
                   "source_evidence": evidence, "instruction": instruction}
        prompt_id = ("workflow.preference_design_check" if self._runtime.recipe.get("preference_design_prompt_version") == 1
                     else "workflow.preference_directed_check")
        for attempt in (0, 1):
            value = self._runtime.ask(key if not attempt else [*key, "format_retry", 1], role,
                prompt_id, request, allow_reasoning_fallback=False,
                prompt_stage="preference")
            try:
                return validate_contrast_assessment(value, row, version=self._runtime.recipe["preference_design_version"])
            except ValueError as error:
                if attempt:
                    raise
                request = {**request, "format_repair": {"code": str(error),
                    "instruction": "Return every required field with boolean flags and verbatim quotes. Recheck the original pair; do not invent a quote or weaken the requested design."}}
