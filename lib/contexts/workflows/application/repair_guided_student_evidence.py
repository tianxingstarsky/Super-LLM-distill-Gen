"""Keep a private judge's broken support references out of the public teaching sequence."""

import json

from lib.prompts.workflow_guided_answers import REPAIR_STUDENT_EVIDENCE, repair_finding_schema
from ..domain.guided_answers import read_teacher_submission
from ..domain.guided_student_evidence import normalize_student_findings


def check_or_repair_student_findings(request, attempt, material, assessment, segment_ids, finding_schema):
    findings = assessment["reusable"]
    normalized, issues = normalize_student_findings(findings, segment_ids)
    if not issues:
        return normalized, None
    audit = attempt["coach_evidence_repair"] = {
        "source": "private_student_assessment", "defects": issues, "checkpoint": {
            "role": "coach_evidence_repair", "index": attempt["attempt"]}}
    packet = {"student_assessment_input": material, "prior_private_assessment": assessment,
              "evidence_protocol_errors": issues}
    response = request("coach_evidence_repair", attempt["attempt"], [
        {"role": "system", "content": REPAIR_STUDENT_EVIDENCE},
        {"role": "user", "content": json.dumps(packet, ensure_ascii=False)}
    ], repair_finding_schema(finding_schema))
    audit["response"] = response
    if response["finish_reason"] != "stop":
        return None, "coach_evidence_repair_incomplete"
    try:
        repair = read_teacher_submission(response)
    except (ValueError, TypeError):
        return None, "invalid_coach_evidence_repair"
    audit["assessment"] = repair
    if (not isinstance(repair, dict) or set(repair) != {"decision", "reason", "reusable"}
            or not isinstance(repair["reason"], str) or not repair["reason"].strip()):
        return None, "invalid_coach_evidence_repair"
    if repair["decision"] == "cannot_verify":
        return None, "coach_evidence_unsubstantiated"
    if repair["decision"] != "correct_evidence":
        return None, "invalid_coach_evidence_repair"
    normalized, issues = normalize_student_findings(repair["reusable"], segment_ids)
    if issues:
        audit["remaining_defects"] = issues
        return None, "invalid_coach_evidence_repair"
    subjects = [item.get("segment_id") if isinstance(item, dict) else None for item in findings]
    if (any(not isinstance(s, str) for s in subjects)
            or set(subjects) != {item["segment_id"] for item in normalized}):
        return None, "coach_evidence_changed_finding_subjects"
    audit["normalized_findings"] = normalized
    return normalized, None
