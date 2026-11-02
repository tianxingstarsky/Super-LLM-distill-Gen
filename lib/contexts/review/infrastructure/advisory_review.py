"""One durable advisory observation; never retry, repair, or grant eligibility."""
from copy import deepcopy
import re

from lib.contexts.models.contracts import (
    BudgetExceeded, ModelJSONError, ModelOutputLimitError, ModelQueueTimeout,
    ModelRequestError, ModelStreamError,
)
from lib.domain.learner_context import learner_contract
from lib.domain.workflow_quality import verdict
from lib.infrastructure.workflow_store import digest
from ..domain.control_text_suggestions import build_control_text_review_context
from .control_text_suggestions import request_with_control_suggestions


def _safe_error(error, fallback):
    code = getattr(error, "code", None) or str(error)
    return code if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,127}", code) else fallback


def review_once(runtime, target, row, evidence, instruction):
    """Read an unchanged candidate once and checkpoint even an unavailable review.

    Original provider output remains in the ordinary request checkpoint. The
    observation is separately durable so resuming cannot buy another review
    after a malformed or unavailable reply. Cancellation and storage/program
    failures are not semantic advice and propagate instead of being swallowed.
    """
    candidate = runtime._review_payload(target, row)
    payload = {"assessment": "single_pass_advisory", "target": target,
        "candidate": candidate, "source_evidence": deepcopy(evidence),
        "task_design_metadata": learner_contract(row.get("qa_contract")),
        "instruction": instruction or ""}
    request_hash = digest(payload)
    key = ["single_pass_advisory_review", target, request_hash]
    observation_key = ["single_pass_advisory_observation", target, request_hash]
    role = "jev" if runtime.recipe.get("package_review", {}).get("enabled") else "generation"
    control_context = build_control_text_review_context(target, row, candidate)

    def observe():
        runtime.check_cancel()
        try:
            raw = request_with_control_suggestions(runtime, key, role, "workflow.review_score", payload,
                control_context=control_context, model_stage="review", prompt_stage="review",
                structured_response_attempts=1)
        except (ModelJSONError, ModelOutputLimitError, ModelStreamError) as error:
            return {"status": "invalid", "error": _safe_error(error, "advisory_response_invalid"),
                    "request_sha256": request_hash, "request_key": deepcopy(key)}
        except (ModelRequestError, ModelQueueTimeout, BudgetExceeded) as error:
            return {"status": "unavailable", "error": _safe_error(error, "advisory_request_unavailable"),
                    "request_sha256": request_hash, "request_key": deepcopy(key)}
        runtime.check_cancel()
        checkpoint = {"stage": runtime.stage, "key": deepcopy(key),
            "checkpoint_sha256": digest(["call", key]), "request_sha256": request_hash,
            "response_sha256": digest(raw)}
        try:
            check = verdict(raw)
        except ValueError as error:
            return {"status": "invalid", "error": _safe_error(error, "advisory_grade_invalid"),
                    "checkpoint": checkpoint, "raw_response": deepcopy(raw)}
        return {"status": "observed", "verdict": deepcopy(check),
                "checkpoint": checkpoint, "raw_response": deepcopy(raw)}

    return runtime.checkpoint(observation_key, observe)
