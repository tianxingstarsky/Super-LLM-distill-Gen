"""Separate training text, private provenance, and actual review evidence."""
from copy import deepcopy
from hashlib import sha256

from lib.domain.workflow_quality import canonical


def content_digest(value) -> str:
    return sha256(canonical(value).encode("utf-8")).hexdigest()


def report_sample(target: str, row: dict, body: dict | None, *, released: bool,
                  position: int) -> dict:
    """Project one record without old drafts, teacher text, or receipt histories.

    Current role/content/tool structures in ``body`` remain literal. Provenance
    describes private material without rendering it as learner-visible text.
    No review is inferred from eligibility, a model name, or a parent receipt.
    """
    digest = content_digest(body) if body is not None else None
    reviews = {key: [] for key in ("fact", "context", "human", "style", "package", "independent", "verification")}
    for key in ("judge", "turn_reviews", "consistency", "preference"):
        if isinstance(row.get(key), (dict, list)):
            reviews["fact"].append({"field": key, "evidence": deepcopy(row[key])})
    if isinstance(row.get("learner_context_check"), dict):
        reviews["context"].append({"field": "learner_context_check", "evidence": deepcopy(row["learner_context_check"])})
    for key in ("style_check", "prefix_review", "qa_contract_check"):
        if isinstance(row.get(key), dict):
            reviews["style"].append({"field": key, "evidence": deepcopy(row[key])})
    repair = row.get("review_repair")
    if isinstance(repair, dict):
        current = {key: deepcopy(repair[key]) for key in (
            "mode", "status", "approved", "score", "score_threshold", "reason", "attempts",
            "candidate_sha256", "verdict", "reviewer", "reviewed_at") if key in repair}
        reviews["human" if repair.get("mode") == "human" else "fact"].append(
            {"field": "review_repair", "evidence": current})
    for key in ("human_review", "manual_review"):
        if isinstance(row.get(key), dict):
            reviews["human"].append({"field": key, "evidence": deepcopy(row[key])})
    if isinstance(row.get("package_review"), dict):
        reviews["package"].append({"field": "package_review", "evidence": deepcopy(row["package_review"])})
    independent = row.get("independent_review")
    if isinstance(independent, dict):
        # An explicit review still needs an exact current-body binding. A stale
        # or incomplete receipt can be displayed, but cannot claim a pass.
        reviews["independent"].append({"field": "independent_review",
            "bound_to_current_body": digest is not None and independent.get("training_body_sha256") == digest,
            "evidence": deepcopy(independent)})
    if isinstance(row.get("verification"), dict):
        reviews["verification"].append({"field": "verification", "evidence": deepcopy(row["verification"])})
    provenance = {key: deepcopy(row[key]) for key in (
        "source_id", "source_name", "source_location", "location", "evidence_level", "reasoning_origin",
        "reasoning_scope", "reasoning_prefill", "answer_response_details", "native_answer_terminal",
        "native_answer_retry", "generation_style") if key in row}
    if "id" in row:
        provenance["record_id"] = deepcopy(row["id"])
    if "source_context" in row:
        source = row["source_context"]
        provenance["teacher_material"] = {"private": True, "sha256": content_digest(source)}
        if isinstance(source, dict):
            provenance["teacher_material"].update({key: deepcopy(source[key]) for key in (
                "source_id", "source_name", "source_kind", "kind", "source_line_ranges", "evidence_level") if key in source})
    contract = row.get("qa_contract")
    if isinstance(contract, dict):
        provenance["task_contract"] = {key: deepcopy(contract[key]) for key in (
            "qa_type", "answer_policy", "source_line_ranges", "knowledge_mode", "communication_goal") if key in contract}
    record = {key: deepcopy(row[key]) for key in ("id", "status", "reason", "source_id", "source_name") if key in row}
    # A failed/incomplete candidate can still retain its real question. Empty
    # output is never reconstructed from prior history or provider reasoning.
    if body is None:
        for key in ("question", "prompt", "messages"):
            if key in row:
                record[key] = deepcopy(row[key])
    return {"id": row.get("id"), "position": position, "target": target,
            "status": row.get("status", "unknown"), "reason": row.get("reason"),
            "released": released, "training_body": deepcopy(body),
            "training_body_sha256": digest, "record": record,
            "reviews": reviews, "provenance": provenance}
