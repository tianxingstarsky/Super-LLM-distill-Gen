"""Score anonymous complete responses, then apply the unchanged release gate."""
from __future__ import annotations

from copy import deepcopy
from typing import Protocol
from ..domain.preference_candidates import (response_projection, response_surface_issue, assessed_preference,
    preference_pair_verdict, contrastive_reasoning_issue, validate_contrast_assessment, contrastive_relationship_issue,
    unscored_preference, rejected_positive)
from ..domain.candidate_content import candidate_digest
from ..domain.reviewed_failure import validate_reviewed_failure_pair, remember_failed_positive
from lib.domain.workflow_quality import accepted


class PreferenceAssessmentPort(Protocol):
    def assess_response(self, projected, evidence, *, response_sha256, instruction): ...
    def assess_contrast(self, row, evidence, *, instruction): ...


class AssessPreferencePair:
    def __init__(self, port: PreferenceAssessmentPort, recipe):
        self._port, self._recipe = port, recipe

    def execute(self, row, evidence, *, instruction=""):
        reused = self._recipe.get("preference_design_version") == 3 and validate_reviewed_failure_pair(row)
        origins = row.get("preference_response_origins") or {}
        responses = [(name, deepcopy(row[name])) for name in ("chosen", "rejected")]
        projections = [response_projection(row, response, origin=origins.get(name)) for name, response in responses]
        # No paid request if either response has invalid roles, missing fields,
        # tool facts or leakage on the actual training surface.
        for projection in projections:
            if issue := response_surface_issue(projection, self._recipe):
                return unscored_preference(row), {"keep": False, "grounded": False, "reasoning_valid": False, "correctness": 1,
                    "scores": {name: 1 for name in ("correctness", "reasoning", "grounding", "instruction", "safety")},
                    "reason": "preference_candidate_surface_invalid: " + issue}, "preference_candidate_surface_invalid"
        assessments = []
        for (_, response), projected in zip(responses, projections):
            identity = candidate_digest(response)
            assessment = self._port.assess_response(projected, evidence,
                response_sha256=identity, instruction="独立核对这份回答与完整推理的正确性、前提和任务满足情况。"
                "正确性指是否正确回答原问题：孤立陈述即便为真，也不能替代原任务的正确回答。只按原问题及依据判断。")
            assessments.append({**deepcopy(assessment), "response_sha256": identity})
            if len(assessments) == 1 and (not accepted(assessment["verdict"])
                    or min(assessment["verdict"]["scores"].values()) / 5 < self._recipe["review_repair"]["score_threshold"]
                    or assessment.get("condition_pending")
                    or (assessment.get("learner_context_check") or {}).get("keep") is False):
                rejected = deepcopy(row)
                if self._recipe.get("preference_design_version") == 3 and self._recipe["preference_design"].get("reuse_reviewed_failures"):
                    remember_failed_positive(rejected, assessments[0], evidence,
                        threshold=self._recipe["review_repair"]["score_threshold"])
                return rejected_positive(rejected, assessments[0], design=self._recipe.get("preference_design"))
        selected, stop = assessed_preference(row, assessments)
        if reused and any(item.get("condition_pending") for item in assessments):
            stop = "condition_support_pending_review"
            selected["preference_assessment"]["stop_reason"] = stop
        verdict = preference_pair_verdict(selected, stop_reason=stop)
        issue = None if reused else contrastive_reasoning_issue(selected, self._recipe.get("preference_design"))
        selected["preference_assessment"]["design_issue"] = issue
        selected["preference_assessment"]["design"] = deepcopy(self._recipe.get("preference_design"))
        if reused:
            selected["preference_assessment"]["negative_source"] = "reviewed_failure"
        if not reused and not stop and verdict["keep"] and not issue:
            contrast = validate_contrast_assessment(self._port.assess_contrast(selected, evidence,
                instruction=instruction), selected, version=self._recipe["preference_design_version"])
            selected["preference_assessment"]["contrast"] = contrast
            issue = contrastive_relationship_issue(contrast, self._recipe.get("preference_design"))
        if issue:
            selected["preference_assessment"]["design_issue"] = issue
            verdict = {**verdict, "keep": False, "reason": verdict["reason"] + "; " + issue}
        return selected, verdict, stop
