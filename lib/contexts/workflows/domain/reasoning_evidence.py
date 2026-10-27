"""Strict incremental evidence with actionable errors and non-waivable observations."""
from copy import deepcopy
from difflib import SequenceMatcher

from .reasoning_checklist import checklist_digest, checklist_evidence_issue
from .reasoning_investigation import ReasoningInvestigation
from .reasoning_tool_contracts import tool_input


class ReasoningEvidence(ReasoningInvestigation):
    def __init__(self, rules, context, candidate):
        super().__init__(rules, context, candidate)
        self.observations, self.interpretations = {}, {}
        self.findings, self.local_cache = [], {}

    def snapshot(self):
        result = super().snapshot()
        failed = any(f["status"] == "violated" for f in self.findings)
        blocked = any(f["status"] == "unknown" for f in self.findings)
        return {**result, "failed": result["failed"] or failed, "blocked": result["blocked"] or blocked,
                "passed": result["passed"] and not failed and not blocked,
                "observations": deepcopy(self.observations), "interpretations": deepcopy(self.interpretations),
                "findings": deepcopy(self.findings)}

    def initial_sources(self, *, max_characters=32000):
        # A small supplied source is cheaper to read once than to buy many searches.
        # Large sources remain addressable without truncating the candidate/reasoning.
        selected, used = [], 0
        for passage in self.passages:
            if passage["id"].startswith(("prompt", "question", "messages")):
                continue
            if used + len(passage["text"]) <= max_characters:
                selected.append(deepcopy(passage))
                used += len(passage["text"])
        return selected

    def _error(self, issue, *, details=None, **extra):
        return {"ok": False, "issue": issue, "pending": self.pending(),
                "recovery": {"action": "Correct only the named invalid entry; keep the candidate, evidence text, units and committed votes unchanged.",
                             "details": details or []}, **extra}

    def execute(self, name, arguments):
        if name == "submit_review":
            if (not isinstance(arguments, dict) or set(arguments) != {"checks", "claims"}
                    or not isinstance(arguments["checks"], list) or not isinstance(arguments["claims"], list)
                    or len(arguments["checks"]) > 32 or len(arguments["claims"]) > 64):
                return self._error("invalid_evidence_submission", details=[{"path": "$", "message": "Use {checks: [...], claims: [...]}; each flat item has the same fields as record_check/record_claim. Do not add a check/claim wrapper."}])
            entries = []
            for field, tool in (("checks", "record_check"), ("claims", "record_claim")):
                for index, entry in enumerate(arguments[field]):
                    receipt = self.execute(tool, entry)
                    entries.append({"path": f"{field}[{index}]", "result": receipt})
            state = self.snapshot()
            return {"ok": bool(entries) and all(e["result"]["ok"] for e in entries), "entries": entries,
                    "pending": state["pending"], "failed": state["failed"], "blocked": state["blocked"],
                    "passed": state["passed"], "issue": None if entries and all(e["result"]["ok"] for e in entries) else "batch_entries_invalid"}
        value, repairs, errors = tool_input(name, arguments)
        if errors:
            return self._error("invalid_tool_arguments", details=errors)
        if name == "record_check":
            result = self._record_check(value)
        elif name == "record_claim":
            result = self._record_claim(value)
        else:
            identity = checklist_digest([name, value])
            if name in {"read_source", "search_sources", "calculate_claim"} and identity in self.local_cache:
                return {**deepcopy(self.local_cache[identity]), "reused_local_receipt": identity}
            result = super().execute(name, value)
            if not result.get("ok"):
                result = self._error(result.get("issue", "invalid_review_operation"), details=self._details(name, value, result))
            elif name in {"read_source", "search_sources", "calculate_claim"}:
                self.local_cache[identity] = deepcopy(result)
        if repairs:
            result["input_repairs"] = repairs
        if not result.get("ok") and name in {"record_check", "record_claim"}:
            field, id_field = ("check", "rule_id") if name == "record_check" else ("claim", "anchor_id")
            entry = value[field]
            identity = entry[id_field]
            result["target"] = {id_field: identity}
            source = self.rules if field == "check" else self.anchors
            requirement = next((r for r in source if r["id"] == identity), None)
            committed = (self.checks if field == "check" else self.claims).get(identity)
            result["recovery"]["target"] = {id_field: identity, "definition": deepcopy(requirement),
                "submitted_status": entry["status"], "committed_record": deepcopy(committed)}
            if committed:
                result["recovery"]["action"] = "This named record is already committed. Do not submit it again or change its verdict. Work only on pending IDs; the saved record remains authoritative."
            elif result["issue"] == "checklist_required_rule_waived":
                result["recovery"]["action"] = "This named rule has allow_na=false. Assess its actual requirement with exact candidate evidence. Use violated/unknown for a genuine gap; do not change unrelated rules or manufacture a positive vote."
        return result

    def _record_check(self, value):
        entry, observations = value["check"], value["observations"]
        rule = next((r for r in self.rules if r["id"] == entry["rule_id"]), None)
        if not rule:
            return self._error("unknown_evidence_id")
        if entry["rule_id"] in self.checks:
            if self.checks[entry["rule_id"]] == entry and self.observations[entry["rule_id"]] == observations:
                return {"ok": True, "reused_committed_evidence": True, "pending": self.pending()}
            return self._error("evidence_already_committed")
        # Each observation refers to an actual part of the frozen requirement.
        covered = [False] * len(rule["requirement"])
        for index, observation in enumerate(observations):
            quote = observation["requirement_quote"]
            start = rule["requirement"].find(quote)
            if start < 0 or not quote.strip():
                differences = [{"operation": op, "submitted": quote[a:b], "expected": rule["requirement"][c:d]}
                    for op,a,b,c,d in SequenceMatcher(None, quote, rule["requirement"], autojunk=False).get_opcodes() if op != "equal"]
                return self._error("observation_requirement_unbound", details=[{"path": f"$.observations[{index}].requirement_quote",
                    "message": "The rule quotation differs from the frozen rule. Exact differences below refer only to the rule text, not to candidate evidence. No text was changed automatically.",
                    "submitted": quote, "expected_requirement": rule["requirement"], "differences": differences}])
            for at in range(start, start + len(quote)):
                covered[at] = True
            mapped = {"satisfied": "met", "violated": "unmet", "unknown": "unmet", "not_applicable": "not_applicable"}[observation["status"]]
            check = {"rule_id": rule["id"], "status": mapped, "evidence": observation["evidence"], "reason": observation["reason"]}
            issue = checklist_evidence_issue([rule], self.candidate, {"checks": [check]})
            if issue:
                return self._error(issue, details=self._details("record_check", {"check": check}, {"issue": issue}))
            if observation["status"] in {"violated", "unknown"}:
                # A valid negative observation survives a malformed sibling or
                # incomplete coverage; a later format repair cannot erase it.
                finding = {"rule_id": rule["id"], **deepcopy(observation)}
                if finding not in self.findings:
                    self.findings.append(finding)
        if any(not covered[i] for i, ch in enumerate(rule["requirement"]) if ch.isalnum()):
            return self._error("observation_requirement_coverage", details=[{"path": "$.observations", "message": "Cover every clause of the frozen requirement, including language, conditions and limitations. A single full requirement quote is valid if its reasoning addresses all clauses."}])
        # Once an actual gap has been reported, a positive summary cannot waive it.
        gaps = [o for o in observations if o["status"] in {"violated", "unknown"}]
        self.observations[rule["id"]] = deepcopy(observations)
        if gaps:
            if all(o["status"] != "unknown" for o in gaps):
                negative = {"rule_id": rule["id"], "status": "unmet", "evidence": [e for o in gaps for e in o["evidence"]],
                            "reason": "\n".join(o["reason"] for o in gaps)}
                super().execute("record_check", {"check": negative})
            state = self.snapshot()
            return {"ok": True, "summary_conflict": entry["status"] != "unmet", "failed": state["failed"],
                    "blocked": state["blocked"], "passed": False, "pending": state["pending"],
                    "message": "Recorded gaps are binding. No reviewer can waive a task requirement."}
        expected = "not_applicable" if all(o["status"] == "not_applicable" for o in observations) else "met"
        if entry["status"] == "unmet":
            # Do not turn a valid negative summary into a format-retry vote.
            # The application asks the independent reviewer before any body repair.
            return super().execute("record_check", {"check": entry})
        if entry["status"] != expected:
            return self._error("check_observation_conflict", details=[{"path": "$.check.status", "message": "The summary must agree with all observations; record a defect in observations if one exists."}])
        result = super().execute("record_check", {"check": entry})
        if not result["ok"]:
            return self._error(result["issue"], details=self._details("record_check", value, result))
        return result

    def _record_claim(self, value):
        claim, interpretation = value["claim"], value["interpretation"]
        anchor = next((a for a in self.anchors if a["id"] == claim["anchor_id"]), None)
        if not anchor:
            return self._error("unknown_evidence_id")
        if claim["anchor_id"] in self.claims:
            if self.claims[claim["anchor_id"]] == claim and self.interpretations[claim["anchor_id"]] == interpretation:
                return {"ok": True, "reused_committed_evidence": True, "pending": self.pending()}
            return self._error("evidence_already_committed")
        context = interpretation["context_quote"]
        # The complete located sentence (including negation) must survive binding.
        if anchor["quote"] not in context or context not in self.candidate[anchor["field"]]:
            return self._error("claim_context_unbound", details=[{"path": "$.interpretation.context_quote", "message": "Copy the complete anchor plus any adjacent condition/negation. Do not bind just the number."}])
        unasserted = interpretation["stance"] in {"negated", "quoted", "non_propositional"}
        if unasserted and (claim["calculations"] or claim["status"] != "not_claim"):
            return self._error("claim_stance_conflict", details=[{"path": "$.claim", "message": "A denied/quoted number is not an asserted computation. Assess the author's actual positive proposition separately; retain all anchors and global rule checks. Do not repair the candidate for a reviewer binding error."}])
        if not unasserted and claim["status"] == "not_claim":
            return self._error("claim_stance_conflict", details=[{"path": "$.interpretation.stance", "message": "An asserted/hypothetical proposition must be checked on its own stated premises, not dismissed as not_claim."}])
        from .reasoning_claims import verify_claims
        receipt = verify_claims([anchor], self.passages, self.candidate, {"checks": [], "claims": [claim]})
        # Invalid reviewer calculations cannot prove a candidate wrong. Still
        # require the same exact operands, units, references and arithmetic
        # before committing; genuine unit/value mismatches remain negatives.
        parameter_errors = {"calculation_schema", "calculation_decimal", "calculation_reported_quote",
            "calculation_reported_unit_substituted", "calculation_unknown_quantity",
            "calculation_scale_mismatch", "calculation_unknown_operation", "calculation_uncited_operand"}
        for item in receipt["items"]:
            for index, calculation in enumerate(item["calculations"]):
                if calculation.get("issue") in parameter_errors:
                    return self._error(calculation["issue"], details=[{"path": f"$.calculations[{index}]",
                        "submitted": deepcopy(claim["calculations"][index]), "verification": calculation,
                        "message": "Correct the review tool inputs, not the candidate. metric_kind/operand_scale describe the operands: counts use quantity/native even when percent_of returns a percent. Cite the exact source for every operand. Preserve the candidate's reported number and literal unit; no successful verdict is implied."}])
        result = super().execute("record_claim", {"claim": claim})
        if result["ok"]:
            self.interpretations[claim["anchor_id"]] = deepcopy(interpretation)
        else:
            return self._error(result["issue"], details=self._details("record_claim", value, result))
        return result

    def _details(self, name, value, result):
        issue = result.get("issue")
        if issue in {"claim_unbound_evidence", "checklist_evidence_not_in_candidate"}:
            entry = value.get("claim", value.get("check", value))
            sources = {p["id"]: p["text"] for p in self.passages} | self.candidate
            bad = []
            for i, cite in enumerate(entry.get("evidence", [])):
                source = cite.get("source", cite.get("field"))
                if source not in sources or cite.get("quote", "") not in sources.get(source, ""):
                    bad.append({"path": f"$.evidence[{i}]", "message": "Citation must be an exact substring in the named source/field, including punctuation. Recopy from the supplied text; no fuzzy match."})
            return bad
        if name == "read_source":
            return [{"path": "$", "message": "Use a listed source_id and a zero-based character start inside its length; length must be 1..10000."}]
        if name == "record_claim":
            from .reasoning_claims import verify_claims
            claim = value["claim"]
            anchors = [a for a in self.anchors if a["id"] == claim["anchor_id"]]
            return [{"path": "$.claim", "message": issue,
                     "verification": verify_claims(anchors, self.passages, self.candidate, {"checks": [], "claims": [claim]})}]
        return [{"path": "$", "message": issue or "Missing evidence cannot pass."}]
