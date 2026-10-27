"""Evidence-backed author self-check followed by an independent rule audit."""
from copy import deepcopy
from ..domain.reasoning_checklist import (
    validate_reasoning_checklist, checklist_digest, checklist_evidence_issue, checklist_passed,
)
from ..domain.reasoning_claims import (
    claim_anchors, source_passages, verify_claims, assessment_contract_issue, assessment_contract_details,
)


class CheckReasoning:
    def __init__(self, port, config):
        self.port = port
        self.config = validate_reasoning_checklist(config)
        if self.config["version"] == 7:
            raise ValueError("generation_reflection_is_not_independent_review")

    def _request(self, key, role, prompt_id, payload, audit, label):
        anchors = payload.get("claim_anchors", [])
        if len(anchors) <= 6:
            return self._request_assessment(key, role, prompt_id, payload, audit, label)
        # Full body and sources remain visible in every request. Only the
        # required clause inventory is bounded. Global rules run once over the
        # full body, then all clause batches must pass the same exact validators.
        merged = {"checks": [], "claims": []}
        batches = audit[label + "_batches"] = []
        for index, start in enumerate(range(0, len(anchors), 6)):
            part = {**payload, "claim_anchors": anchors[start:start + 6],
                    "rules": payload["rules"] if index == 0 else [],
                    "audit_partition": {"index": index, "count": (len(anchors) + 5) // 6}}
            response = self._request_assessment([*key, "claim_batch_v1", index,
                                          checklist_digest(part["claim_anchors"])],
                role, prompt_id, part, audit, label)
            checks = {"checks": response.get("checks")} if isinstance(response, dict) else response
            issue = checklist_evidence_issue(part["rules"], part["candidate"], checks)
            receipt = verify_claims(part["claim_anchors"], part["source_passages"], part["candidate"], response)
            batches.append({"anchor_ids": [a["id"] for a in part["claim_anchors"]],
                            "response": deepcopy(response), "rule_issue": issue, "verification": receipt})
            if issue or not receipt["passed"] or not checklist_passed(response):
                # The remaining batches were deliberately not purchased. Their
                # absent coverage must not hide the actual failed check.
                audit[label + "_batch_failure"] = {
                    "index": index,
                    "issue": issue or receipt["issue"] or "checklist_found_gap",
                }
                # Keep the failed raw part. Aggregate verification will also
                # fail incomplete coverage; an unfinished audit cannot pass.
                if isinstance(response, dict) and set(response) == {"checks", "claims"}:
                    merged["checks"].extend(response["checks"] if isinstance(response["checks"], list) else [])
                    merged["claims"].extend(response["claims"] if isinstance(response["claims"], list) else [])
                    return merged
                return response
            merged["checks"].extend(response["checks"])
            merged["claims"].extend(response["claims"])
        return merged

    def _request_assessment(self, key, role, prompt_id, payload, audit, label):
        response = self.port.request(key, role, prompt_id, payload, allow_reasoning_fallback=False)
        if "claim_anchors" not in payload:
            return response
        issue = assessment_contract_issue(payload["rules"], payload["claim_anchors"],
            payload["source_passages"], payload["candidate"], response)
        if not issue:
            return response
        entry = {"issue": issue, "original_assessment": deepcopy(response),
                 "anchor_ids": [a["id"] for a in payload["claim_anchors"]]}
        audit.setdefault(label + "_format_repairs", []).append(entry)
        # One bounded correction of the judge's own invalid representation.
        # The complete candidate stays byte-identical and negative semantic
        # judgments are never resampled by this path.
        repair = {"validation_issue": issue, "previous_assessment": response,
                  "validation_details": assessment_contract_details(payload["rules"], payload["claim_anchors"],
                      payload["source_passages"], payload["candidate"], response)}
        # Bind the correction to the complete diagnostic. A first-error-only
        # receipt cannot stand in for this request on a resumed task.
        repaired = self.port.request([*key, "assessment_format_repair_v2", checklist_digest(repair)],
            role, prompt_id, {**payload, "assessment_repair": repair}, allow_reasoning_fallback=False)
        entry["repaired_assessment"] = deepcopy(repaired)
        return repaired

    def execute(self, key, context, candidate, *, phase):
        if self.config["version"] == 6:
            from .review_semantic_reasoning import ReviewSemanticReasoning
            return ReviewSemanticReasoning(self.port, self.config).execute(key, context, candidate, phase=phase)
        if self.config["version"] in (3, 4, 5):
            from .investigate_reasoning import InvestigateReasoning
            return InvestigateReasoning(self.port, self.config).execute(key, context, candidate, phase=phase)
        body = {name: candidate[name] for name in ("reasoning", "answer") if name in candidate}
        rules = self.config["rules"]
        version = self.config["version"]
        binding = {"version": version, "phase": phase, "candidate_sha256": checklist_digest(body),
                   "rules_sha256": checklist_digest(rules)}
        payload = {"context": context, "candidate": body, "rules": rules, "phase": phase}
        audit = {**binding, "rules": deepcopy(rules), "passed": False}
        identity = [binding["candidate_sha256"], binding["rules_sha256"]]
        if version == 2:
            payload.update(claim_anchors=claim_anchors(body), source_passages=source_passages(context))
            audit.update(claim_anchors=deepcopy(payload["claim_anchors"]), context_sha256=checklist_digest(context))
            identity += ["claims_v2", audit["context_sha256"]]
        def evidence_issue(value, label):
            checks = {"checks": value.get("checks")} if version == 2 and isinstance(value, dict) else value
            issue = checklist_evidence_issue(rules, body, checks)
            if version == 2:
                receipt = verify_claims(payload["claim_anchors"], payload["source_passages"], body, value)
                audit[label + "_verification"] = receipt
                issue = issue or (receipt["issue"] if not receipt["passed"] else None)
            return audit.get(label + "_batch_failure", {}).get("issue") or issue
        authored = self._request([*key, "self_check", *identity],
            "generation", "workflow.cot_claim_check" if version == 2 else "workflow.cot_self_check",
            payload, audit, "self_check")
        audit["self_check"] = deepcopy(authored)
        if issue := evidence_issue(authored, "self_check"):
            return {**audit, "issue": issue}
        if not checklist_passed(authored):
            return {**audit, "issue": "checklist_author_found_gap"}
        # Hide the author's positive verdict to reduce agreement/confirmation
        # pressure. The reviewer independently inspects the exact same body.
        reviewed = self._request([*key, "independent_check", *identity],
            "jev", "workflow.cot_claim_review" if version == 2 else "workflow.cot_self_check_review",
            payload, audit, "independent_check")
        audit["independent_check"] = deepcopy(reviewed)
        if issue := evidence_issue(reviewed, "independent_check"):
            return {**audit, "issue": issue}
        if not checklist_passed(reviewed):
            return {**audit, "issue": "checklist_reviewer_found_gap"}
        return {**audit, "passed": True}
