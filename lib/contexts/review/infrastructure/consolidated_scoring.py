"""Bind consolidated review to a frozen candidate and its actual receipts."""
from copy import deepcopy

from lib.infrastructure.workflow_store import digest
from .review_style import current_claim_review_requirements
from ..domain.consolidated_claim_review import consolidated_review_requests
from ..domain.control_text_suggestions import build_control_text_review_context


def score_consolidated_claims(runtime, target, row, evidence, instruction):
    from lib.bootstrap.automatic_review import consolidated_review

    payload = runtime._review_payload(target, row)
    requests = consolidated_review_requests(target, payload, evidence,
        reasoning_required=runtime.recipe.get("reasoning_required", False),
        review_requirements=current_claim_review_requirements(runtime, target, row, instruction=instruction) or None)
    audit = {"version": 6, "method": "consolidated_condition_claim_review",
        "candidate_sha256": digest(payload), "source_input_sha256": digest(evidence),
        "source_evidence_sha256": requests[0]["scope"]["source_evidence_sha256"],
        "status": "pending", "assessments": [], "schema_failures": []}
    row["claim_review"] = audit
    reviewer = consolidated_review(runtime,
        role="jev" if runtime.recipe.get("package_review", {}).get("enabled") else "generation",
        audit=audit, control_context=build_control_text_review_context(target, row, payload))
    check = reviewer.execute(payload, requests, audit)
    row["reasoning_assessment_method"] = "llm_claim_review"
    row["reasoning_assessment_reference"] = {"kind": "consolidated_claim_review",
        "candidate_sha256": audit["candidate_sha256"], "source_input_sha256": audit["source_input_sha256"],
        "source_evidence_sha256": audit["source_evidence_sha256"],
        "checkpoints": [deepcopy(part["checkpoint"]) for entry in audit["assessments"]
            for part in ([entry, entry["focused"]] if "focused" in entry else [entry])]}
    return runtime._claim_refutation_release(row, check)
