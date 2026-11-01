"""Checkpointed grouped review; reuse the existing provider and advice transport."""
from copy import deepcopy
import json
import re

from lib.infrastructure.workflow_store import digest
from ..domain.consolidated_claim_review import (
    consolidated_checkpoint_key, validate_consolidated_claim_review,
)
from ..domain.wire_schema import (
    CLAIM_REVIEW_DYNAMIC_WIRE_HEADER, claim_review_response_schema,
    schema_diagnostics, source_leaf_paths,
)
from .control_text_suggestions import request_with_control_suggestions


def _response_schema(request):
    """Reuse the established evidence/premise wire shapes, without fake calls."""
    legacy_shape = {**request, "claim_review_protocol_version": 4,
                    "phase": "claim_adjudication", "challenge_review": {"issues": []}}
    legacy = claim_review_response_schema(legacy_shape)
    fields = deepcopy(legacy["properties"]["claim_checks"]["items"]["properties"])
    fields.pop("unit_id")
    fields = {"unit_ids": {"type": "array", "minItems": 1, "uniqueItems": True,
                          "items": {"type": "string", "enum": request["review_unit_ids"]}}, **fields}
    properties = {
        "groups": {"type": "array", "minItems": 1, "items": {
            "type": "object", "properties": fields, "required": list(fields), "additionalProperties": False}},
        "coverage": legacy["properties"]["coverage"],
        "verdict": legacy["properties"]["verdict"],
        "limitations": {"type": "array", "items": {"type": "string", "minLength": 1}},
    }
    return {"$schema": legacy["$schema"], "type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False, "$defs": legacy["$defs"]}


def _format_feedback(raw, request, error, schema):
    """Report owned schema paths and ids; never echo arbitrary exception text."""
    code = str(error)
    if re.fullmatch(r"[a-z][a-z0-9_]{0,127}", code) is None:
        code = "invalid_consolidated_review_response"
    diagnostics = schema_diagnostics(raw, schema)
    if code == "invalid_consolidated_unit_coverage" and isinstance(raw, dict):
        seen = [identifier for group in raw.get("groups", []) if isinstance(group, dict)
                and isinstance(group.get("unit_ids"), list) for identifier in group["unit_ids"]
                if isinstance(identifier, str)] if isinstance(raw.get("groups"), list) else []
        diagnostics["missing_unit_ids"] = [identifier for identifier in request["review_unit_ids"] if identifier not in seen]
        diagnostics["repeated_unit_ids"] = sorted({identifier for identifier in seen
                                                   if identifier in request["review_unit_ids"] and seen.count(identifier) > 1})
    return code, {"code": "invalid_consolidated_review_response", "instruction": (
        "The first response has a concrete protocol defect: " + code + ". Return the same complete grouped-review "
        "contract with that defect corrected. Preserve substantive judgments; missing evidence stays insufficient. "
        "Do not invent facts, premises, source text, or passing scores to repair a format error. "
        "Every requested unit must appear exactly once. Program-owned diagnostics: "
        + json.dumps(diagnostics, ensure_ascii=False, separators=(",", ":")))}


class CheckpointedConsolidatedReviewAdapter:
    """At most one schema retry; focused semantic follow-up belongs to the use case."""

    def __init__(self, runtime, *, role, audit, control_context=None):
        self.runtime = runtime
        self.role, self.audit, self.control_context = role, audit, control_context

    def check_cancel(self):
        self.runtime.check_cancel()

    def review(self, request):
        schema = _response_schema(request)
        instruction = (CLAIM_REVIEW_DYNAMIC_WIRE_HEADER
            + "This is the complete inner quality-response schema for consolidated protocol 6. "
              "It is wire guidance; it does not claim the provider enforces JSON Schema. "
              "Return only the semantic fields shown. Source paths are relative to source_evidence; "
              "context indices are actual messages array indices. All requested unit ids must occur exactly once.\n"
            + json.dumps({"response_json_schema": schema, **source_leaf_paths(request["source_evidence"])},
                         ensure_ascii=False, separators=(",", ":")))
        base_key = consolidated_checkpoint_key(request)
        feedback = None
        for attempt in (1, 2):
            self.check_cancel()
            key = base_key if attempt == 1 else [*base_key, "format_repair", 1]
            payload = request if feedback is None else {**request, "format_repair": feedback}
            raw = request_with_control_suggestions(self.runtime, key, self.role, "workflow.review_score", payload,
                control_context=self.control_context, model_stage="review", prompt_stage="review",
                instruction=instruction, structured_response_attempts=1)
            checkpoint = {"stage": self.runtime.stage, "key": deepcopy(key),
                "checkpoint_sha256": digest(["call", key]), "request_sha256": digest(payload),
                "review_request_sha256": digest(request), "response_sha256": digest(raw),
                **({"format_repair": deepcopy(feedback)} if feedback is not None else {})}
            try:
                receipt = validate_consolidated_claim_review(raw, request)
            except ValueError as error:
                code, feedback = _format_feedback(raw, request, error, schema)
                self.audit["schema_failures"].append({"phase": request["phase"],
                    "message_index": request["scope"]["message_index"], "attempt": attempt,
                    "code": code, "checkpoint": checkpoint})
                self.audit["status"] = "format_retry_pending" if attempt == 1 else "invalid_after_retry"
                if attempt == 2:
                    raise
            else:
                return receipt, checkpoint
