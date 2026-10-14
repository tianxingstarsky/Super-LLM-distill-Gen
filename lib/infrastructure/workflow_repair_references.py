"""Pinned auxiliary documents for a selected same-task correction.

File hashes prove snapshot identity, not factual authority. These documents are
private teacher memory; the selected learner messages and parent stay fixed.
"""
from copy import deepcopy
import re


def append_repair_reference_sources(workflow, evidence):
    if (workflow.recipe.get("repair_reference_sources_version") != 1
            or not (workflow.recipe.get("review_repair") or {}).get("reference_sources")):
        return evidence
    if not workflow.recipe.get("repair_inputs") or not isinstance(evidence, dict):
        raise ValueError("review_reference_source_unavailable")
    from lib.doc2corpus import import_text
    from lib.infrastructure.training_workflow import file_hash, digest
    from lib.domain.workflow_scale import MAX_CONTEXT_WINDOW_TOKENS
    references, receipts, reference_ranges = [], [], []
    for source in workflow.recipe.get("sources", []):
        name = source.get("file")
        if not isinstance(name, str) or not re.fullmatch(r"[0-9]{4}\.(?:txt|md)", name):
            raise ValueError("review_reference_source_unavailable")
        file = workflow.path / "inputs" / name
        if file.is_symlink() or not file.is_file() or file_hash(file) != source.get("sha256"):
            raise ValueError("review_reference_source_changed")
        text = import_text(file, source_processing_version=workflow.recipe.get("source_processing_version", 1))
        if not text.strip():
            raise ValueError("review_reference_source_unavailable")
        references.append(text)
        receipts.append({"file": name, "sha256": source["sha256"], "text_sha256": digest(text)})
        reference_ranges.append({"material_id": source["sha256"], "start_line": 1, "end_line": len(text.splitlines())})
    if not references:
        raise ValueError("review_reference_source_unavailable")
    result = deepcopy(evidence)
    original = result.get("teacher_evidence_blocks") or [result["teacher_evidence"]]
    result["teacher_evidence_blocks"] = [*original, *references]
    result["teacher_evidence_source_ids"] = [*([evidence.get("source_id")] * len(original)),
                                             *(source["sha256"] for source in workflow.recipe["sources"])]
    if result.get("source_line_scope") == "selected":
        if len(result.get("source_line_ranges", [])) != len(original):
            raise ValueError("invalid_source_line_ranges")
        result["source_line_ranges"] = [*result["source_line_ranges"], *reference_ranges]
    result["teacher_evidence"] = "\n\n".join(result["teacher_evidence_blocks"])
    if len(result["teacher_evidence"]) > MAX_CONTEXT_WINDOW_TOKENS:
        raise ValueError("review_reference_source_capacity_exceeded")
    result["supplemental_reference_sources"] = receipts
    result["evidence_scope"] = "authenticated_parent_and_pinned_auxiliary_documents"
    return result
