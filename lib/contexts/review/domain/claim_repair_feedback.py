"""Exact, validated dispute spans for an explicitly requested content repair."""
from __future__ import annotations

from copy import deepcopy
from .candidate_content import candidate_digest
from .claim_windows import validate_windowed_claim_audit


def claim_repair_feedback(payload, evidence, audit, *, include_quality_feedback=False):
    """Feedback is a reviewer opinion, never proof or invented task conditions.

    A manual edit can supersede an earlier audit. In that case no stale span
    is projected onto the new body. An audit claiming to match must validate
    completely, including the raw windows and source binding.
    """
    if not isinstance(audit, dict) or audit.get("version") != 5:
        return None
    if audit.get("candidate_sha256") != candidate_digest(payload):
        return None
    if audit.get("source_input_sha256") != candidate_digest(evidence):
        raise ValueError("stale_claim_repair_evidence")
    checked = validate_windowed_claim_audit(payload, audit["derived_verdict"], audit)
    disputes = []
    for assessment in checked["assessments"]:
        review = assessment["review"]
        request = review["request"]
        units = {unit["id"]: unit for unit in request["target_units"]}
        guards = {entry["unit_id"]: entry for entry in review["condition_guard"]["checks"]}
        for claim in review["assembled_response"]["claim_checks"]:
            guard = guards[claim["unit_id"]]
            if guard["effective_status"] == "supported":
                continue
            related = {premise["unit_id"] for premise in claim["premises"]
                       if premise["origin"] == "candidate"}
            disputes.append({"message_index": request["scope"]["message_index"],
                "unit": deepcopy(units[claim["unit_id"]]),
                "reviewer_claim": deepcopy(claim), "condition_provenance": deepcopy(guard),
                "related_candidate_units": [deepcopy(unit) for unit in units.values() if unit["id"] in related]})
    packet = {"version": 1, "candidate_sha256": audit["candidate_sha256"],
        "source_input_sha256": audit["source_input_sha256"], "audit_sha256": candidate_digest(audit),
        "semantic_verification": "provenance_only_not_entailment", "reviewer_opinions_not_facts": True,
        "disputes": disputes}
    if include_quality_feedback:
        packet["quality_feedback"] = [
            {"message_index": item["review"]["request"]["scope"]["message_index"],
             "unit_ids": deepcopy(window["review"]["request"]["review_window"]["unit_ids"]),
             "verdict": deepcopy(window["review"]["raw_response"]["verdict"])}
            for item in checked["assessments"] for window in item["windows"]
            if (window["review"]["raw_response"]["verdict"]["keep"] is False
                or min(window["review"]["raw_response"]["verdict"]["scores"].values()) < 4)]
    return packet
