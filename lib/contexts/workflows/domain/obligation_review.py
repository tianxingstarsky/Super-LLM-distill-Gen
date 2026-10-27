"""Opt-in evidence policy 2: inspect actual risks and every training turn."""
from copy import deepcopy
import re

from .evidence_scoped_review import EvidenceScopedReview
from .reasoning_checklist import checklist_digest
from .semantic_review import CITATION, TEXT, _array, _object, _number_present
from .review_obligations import reviewed_dialogue, review_obligations, quantity_issue, HYPOTHETICAL


class ObligationReview(EvidenceScopedReview):
    def __init__(self, rules, context, candidate, *, phase, disputes=None):
        body = reviewed_dialogue(context, candidate)
        super().__init__(rules, context, body, phase=phase, disputes=disputes)
        self.obligations = review_obligations(context, body)
        self.prefix = None  # Policy-1 prefix proofs never certify policy-2 obligations.
        self.binding = checklist_digest([self.binding, "risk_obligations_v3_numeric_feedback", self.obligations])

    def tools(self):
        tools = super().tools()
        if self.disputes is None:
            schema = tools[0]["function"]["parameters"]
            number = {"type": "string", "pattern": r"^-?\d+(?:\.\d+)?$",
                      "description": "Only the exact decimal number, e.g. '44.4'. No units, model names, %, approximations or prose. Put those in reason and evidence."}
            quantity = _object({"operation": {"type": "string", "enum": ["percentage_points", "relative_percent", "score_points"]},
                                "baseline": deepcopy(number), "current": deepcopy(number), "reported": deepcopy(number)})
            schema["properties"]["risk_checks"] = _array(_object({
                "id": {"type": "string", "enum": [o["id"] for o in self.obligations]},
                "status": {"type": "string", "enum": ["supported", "defect", "uncertain", "not_applicable"]},
                "evidence": _array(CITATION), "reason": TEXT, "quantities": _array(quantity)}))
            schema["required"].append("risk_checks")
            if not self.obligations:
                schema["properties"]["risk_checks"]["items"]["properties"]["id"].pop("enum")
                schema["properties"]["risk_checks"]["maxItems"] = 0
            tools[0]["function"]["description"] += (
                " Also complete environment.risk_obligations once. Return risk_checks=[] if none. "
                "For a supported quantitative change, quantities records the cited baseline/current, "
                "reported change and correct unit; evidence includes original metric/axis and values. "
                "Non-quantitative checks use quantities=[]. Negative risk verdicts need a matching finding; "
                "do not change a negative vote to work around a tool error.")
        return tools

    def environment(self, *, role, remaining_turns):
        return {**super().environment(role=role, remaining_turns=remaining_turns),
                "evidence_policy": 2, "risk_obligations": deepcopy(self.obligations),
                "review_scope": "All candidate surfaces, including every history_* assistant turn. "
                    "A correction in the last answer does not remove erroneous earlier supervised targets. "
                    "History defects require a separate whole-dialogue repair, not another last-answer rewrite.",
                "risk_contract": "Supported history checks cite that actual history turn. Scope checks cite the actual user message and candidate attribution. "
                    "Bilingual checks cite the actual reasoning where both languages explain a consequential distinction. "
                    "Quantity checks cite metric/axis AND comparison values from external evidence; copying ambiguous prose is insufficient. "
                    "Routine percentages, counterexamples or endpoints may be not_applicable only with explicit body evidence and an explanation. "
                    "Use defect for known wrong units/missing mandatory work; uncertainty for missing source or indeterminate scope. "
                    "Every negative risk requires a finding attached to an active rule and the same body surface."}

    def validate_review(self, review, *, error_details=None):
        return self._validate_review(review, complete=True, error_details=error_details)

    def validate_review_entries(self, review):
        return self._validate_review(review, complete=False)

    def _validate_review(self, review, *, complete, error_details=None):
        if not isinstance(review, dict) or set(review) != {"checks", "findings", "covered_sections", "risk_checks"}:
            return "risk_review_fields"
        if issue := super()._validate_review({k: v for k, v in review.items() if k != "risk_checks"}, complete=complete):
            return issue
        expected = {o["id"]: o for o in self.obligations}
        risks = review["risk_checks"]
        if not isinstance(risks, list) or complete and len(risks) != len(expected):
            return "risk_coverage_missing"
        seen = set()
        def reject(issue, index):
            if error_details is not None:
                error_details.append({"path": f"$.risk_checks[{index}]", "message": issue,
                    "risk_id": risks[index].get("id") if isinstance(risks[index], dict) else None,
                    "instruction": "Correct this entry using actual evidence. Numeric fields contain decimal strings only. "
                        "Keep genuine defects or uncertainty as negative findings; never invent operands or waive the risk."})
            return issue
        for index, check in enumerate(risks):
            if (not isinstance(check, dict) or set(check) != {"id", "status", "evidence", "reason", "quantities"}
                    or not isinstance(check["id"], str) or check["id"] not in expected or check["id"] in seen
                    or check["status"] not in ("supported", "defect", "uncertain", "not_applicable")
                    or not isinstance(check["reason"], str) or not check["reason"].strip()
                    or not isinstance(check["quantities"], list)):
                return reject("invalid_risk_check", index)
            seen.add(check["id"])
            obligation = expected[check["id"]]
            citations = check["evidence"]
            if not self._citations_valid(citations) or not any(c["source"] in self.candidate for c in citations):
                return reject("risk_evidence_not_bound", index)
            body = [c for c in citations if c["source"] in self.candidate]
            if obligation["kind"] == "history" and not any(c["source"] in obligation["fields"] for c in body):
                return reject("history_turn_not_reviewed", index)
            if obligation["kind"] == "quantity" and not any(c["source"] == obligation["field"] and
                    (c["quote"] in obligation["quote"] or obligation["quote"] in c["quote"]) for c in body):
                return reject("quantity_claim_not_reviewed", index)
            if check["status"] in ("defect", "uncertain"):
                category = "defect" if check["status"] == "defect" else "insufficient_evidence"
                if complete and not any(f["category"] == category and any(f["body"]["source"] == c["source"] and
                        (c["quote"] in f["body"]["quote"] or f["body"]["quote"] in c["quote"]) for c in body)
                           for f in review["findings"]):
                    return reject("negative_risk_requires_finding", index)
                continue
            if check["status"] == "not_applicable":
                if obligation["kind"] != "quantity" or not HYPOTHETICAL.search(obligation["quote"]):
                    return reject("required_risk_waived", index)
                continue
            if obligation["kind"] == "quantity":
                external = [c for c in citations if c["source"].startswith(("source", "source_context"))]
                if not external or not check["quantities"]:
                    return reject("quantity_metric_and_operands_required", index)
                evidence = "\n".join(c["quote"] for c in external)
                for quantity in check["quantities"]:
                    if issue := quantity_issue(quantity, evidence, obligation["quote"]):
                        return reject(issue, index)
            elif check["quantities"]:
                return reject("nonquantity_risk_has_calculation", index)
            if obligation["kind"] == "scope" and not any(
                    c["source"] in (f"prompt[{obligation['user_turn']}].content", f"messages[{obligation['user_turn']}].content")
                    for c in citations):
                return reject("visible_scope_requires_user_evidence", index)
            if obligation["kind"] == "bilingual":
                proof = "\n".join(c["quote"] for c in body if c["source"] == "reasoning")
                if not re.search(r"[\u4e00-\u9fff]", proof) or not re.search(r"\b[A-Za-z]{2,}\b", proof):
                    return reject("bilingual_reasoning_evidence_missing", index)
        return None

    def _execute(self, name, arguments):
        if name == "submit_semantic_review" and self.disputes is None:
            # The base representation diagnostics do not know the new risk field.
            from .semantic_review_feedback import schema_feedback, review_feedback
            details = schema_feedback(self.tools()[0]["function"]["parameters"], arguments)
            details += review_feedback(self, arguments)
            issue = self.validate_review(arguments, error_details=details)
            if details or issue:
                return self._error(issue or "invalid_tool_arguments", details=details or [{
                    "path": "$.risk_checks", "message": issue,
                    "required_risks": [o["id"] for o in self.obligations],
                    "instruction": "Keep actual verdicts. Supply literal body and external evidence; do not fabricate operands, translations or source scope. A negative verdict needs a matching finding."}])
            if self.submission is not None:
                return {"ok": arguments == self.submission, "reused_committed_evidence": True}
            self.submission = deepcopy(arguments)
            return {"ok": True, "committed": True, "pending": self.pending()}
        result = super()._execute(name, arguments)
        if name == "calculate_numbers" and result.get("issue") == "uncited_operand":
            evidence = "\n".join(c["quote"] for c in arguments["evidence"])
            return self._error("uncited_operand", details=[{
                "path": "$." + field, "value": arguments[field],
                "message": "This operand is absent from the cited text.",
                "instruction": "Read/search the actual source containing this number, or cite an existing computed value. "
                    "Do not change the operand to fit an unrelated snippet or claim an unexecuted calculation."}
                for field in ("left", "right") if not _number_present(arguments[field], evidence)])
        return result
