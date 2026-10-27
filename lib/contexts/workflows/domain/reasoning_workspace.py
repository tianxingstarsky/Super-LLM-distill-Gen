"""Version 5 evidence workspace: explicit applicability, immutable facts and typed feedback."""
from copy import deepcopy

from .reasoning_assertions import assertion_anchors, assertion_receipt
from .reasoning_checklist import checklist_digest, checklist_evidence_issue
from .reasoning_environment import compile_requirements, review_environment
from .reasoning_environment_tools import environment_tools, environment_tool_input, normalize_citation_keys
from .reasoning_evidence import ReasoningEvidence
from .reasoning_investigation import ReasoningInvestigation
from .reasoning_references import citation_diagnostics, inspect_claim, compare_claim_ratio, resolve_citation_ids


class ReasoningWorkspace(ReasoningEvidence):
    def __init__(self, rules, context, candidate, *, phase):
        self.phase = phase
        self.requirements = compile_requirements(rules, phase)
        self.frozen_rules = deepcopy(rules)
        active = {r["rule_id"] for r in self.requirements if r["active"]}
        effective = [{k: deepcopy(v) for k, v in r.items() if k != "phases"} for r in rules if r["id"] in active]
        for rule in effective:
            rule["requirement"] = "；".join(r["requirement"] for r in self.requirements
                if r["active"] and r["rule_id"] == rule["id"])
        super().__init__(effective, context, candidate)
        self.anchors = assertion_anchors(candidate)
        self.binding = checklist_digest([5, "evidence-recovery-5", phase, rules, context, candidate, self.requirements])
        self.assertions, self.operation_receipts = {}, {}
        self.stopped_incomplete = False
        self.operation_metrics = {"executed": 0, "reused": 0}

    def tools(self):
        return environment_tools()

    def environment(self, *, role, remaining_turns):
        return review_environment(self, role=role, remaining_turns=remaining_turns)

    def snapshot(self):
        state = super().snapshot()
        failed = any(r["status"] == "violated" for r in self.assertions.values())
        blocked = self.stopped_incomplete or any(r["status"] == "unresolved" for r in self.assertions.values())
        return {**state, "failed": state["failed"] or failed, "blocked": state["blocked"] or blocked,
                "passed": state["passed"] and not failed and not blocked,
                "assertions": deepcopy(self.assertions), "requirements": deepcopy(self.requirements),
                "operation_metrics": deepcopy(self.operation_metrics)}

    def _error(self, issue, *, details=None, **extra):
        result = super()._error(issue, details=details, **extra)
        result["category"] = "tool_input_error"
        result["candidate_changed"] = False
        return result

    def _details(self, name, value, result):
        if result.get("issue") in {"claim_unbound_evidence", "checklist_evidence_not_in_candidate"}:
            entry = value.get("claim", value.get("check", value))
            return citation_diagnostics(entry.get("evidence", []), {p["id"]: p["text"] for p in self.passages} | self.candidate)
        return super()._details(name, value, result)

    def execute(self, name, arguments):
        # Resolve per batch item so one invalid ID cannot discard valid siblings.
        span_repairs = []
        if name != "submit_review":
            arguments, span_repairs, span_errors = resolve_citation_ids(arguments, self.anchors)
            if span_errors:
                return self._error("unknown_or_conflicting_citation_id", details=span_errors)
        arguments, alias_repairs = normalize_citation_keys(arguments)
        alias_repairs = span_repairs + alias_repairs
        # Batch validation is per entry: a malformed sibling never discards valid work.
        if name == "submit_review":
            if (not isinstance(arguments, dict) or set(arguments) != {"checks", "claims"}
                    or any(not isinstance(arguments[k], list) for k in ("checks", "claims"))
                    or len(arguments["checks"]) > 32 or len(arguments["claims"]) > 64):
                return self._error("invalid_evidence_submission")
            entries = [{"path": f"{field}[{i}]", "result": self.execute(tool, item)}
                       for field, tool in (("checks", "record_check"), ("claims", "record_claim"))
                       for i, item in enumerate(arguments[field])]
            state = self.snapshot()
            ok = bool(entries) and all(e["result"]["ok"] for e in entries)
            return {"ok": ok, "entries": entries, "pending": state["pending"],
                    "failed": state["failed"], "blocked": state["blocked"], "passed": state["passed"],
                    "category": self._category(state) if ok else "tool_input_error",
                    "input_repairs": alias_repairs,
                    "issue": None if ok else "batch_entries_invalid"}
        errors = environment_tool_input(name, arguments)
        if errors:
            return self._error("invalid_tool_arguments", details=errors)
        if name in {"inspect_claim", "compare_claim_ratio"}:
            identity = checklist_digest([name, arguments])
            if identity in self.operation_receipts:
                self.operation_metrics["reused"] += 1
                result = {**deepcopy(self.operation_receipts[identity]), "reused_local_receipt": identity}
            else:
                result = (inspect_claim(self.anchors, self.candidate, arguments["anchor_id"]) if name == "inspect_claim"
                          else compare_claim_ratio(self.anchors, self.candidate, self.passages, arguments))
                if not result["ok"]:
                    result = self._error(result["issue"], details=[result])
                self.operation_receipts[identity] = deepcopy(result)
                self.operation_metrics["executed"] += 1
        elif name == "record_check":
            result = self._record_requirements(arguments)
        elif name == "record_claim":
            identity = arguments["anchor_id"]
            if identity in self.claims:
                saved = self.claims[identity]
                same_claim = all(saved[k] == arguments[k] for k in ("anchor_id", "status", "evidence", "calculations"))
                same_context = all(self.interpretations[identity][k] == arguments["interpretation"][k] for k in ("stance", "context_quote"))
                if same_claim and same_context:
                    return {"ok": True, "reused_committed_evidence": True, "pending": self.pending(),
                            "category": self._category(self.snapshot())}
            result = super().execute(name, arguments)
            if result.get("ok"):
                anchor = next(a for a in self.anchors if a["id"] == arguments["anchor_id"])
                receipt = assertion_receipt(anchor, arguments, arguments["interpretation"])
                if receipt:
                    self.assertions[anchor["id"]] = receipt
        elif name == "finish_review":
            state = self.snapshot()
            self.stopped_incomplete |= not (state["passed"] or state["failed"] or state["blocked"])
            result = {"ok": True, "issue": "review_finished_with_pending_evidence" if self.stopped_incomplete else None}
        else:
            # Scope is this exact candidate/context/rule/phase binding. A new draft
            # cannot reuse semantic decisions or stale failures from this cache.
            identity = checklist_digest([name, arguments])
            if name in {"read_source", "search_sources", "calculate_claim"}:
                if identity in self.operation_receipts:
                    self.operation_metrics["reused"] += 1
                    result = {**deepcopy(self.operation_receipts[identity]), "reused_local_receipt": identity}
                else:
                    result = super().execute(name, arguments)
                    if name == "calculate_claim" and result.get("ok"):
                        receipt = result.get("receipt", {})
                        parameter_errors = {"calculation_schema", "calculation_decimal", "calculation_reported_quote",
                            "calculation_reported_unit_substituted", "calculation_unknown_quantity",
                            "calculation_scale_mismatch", "calculation_unknown_operation", "calculation_uncited_operand"}
                        if receipt.get("issue") in parameter_errors:
                            result = self._error(receipt["issue"], details=[{"path": "$.calculation",
                                "submitted": deepcopy(arguments["calculation"]), "verification": receipt,
                                "message": "Only fix calculation inputs. Counts use quantity/native; the reported quote must belong to this anchor. For a cross-sentence half comparison without a reported percentage use compare_claim_ratio. No candidate verdict was made."}])
                    self.operation_metrics["executed"] += 1
                    self.operation_receipts[identity] = deepcopy(result)
            else:
                result = super().execute(name, arguments)
        state = self.snapshot()
        if alias_repairs:
            result["input_repairs"] = [*result.get("input_repairs", []), *alias_repairs]
        if result.get("ok"):
            result.update(pending=state["pending"], failed=state["failed"], blocked=state["blocked"],
                          passed=state["passed"], category=self._category(state))
            if name == "record_claim" and arguments["anchor_id"] in self.assertions:
                result["assertion_verification"] = deepcopy(self.assertions[arguments["anchor_id"]])
            if name in {"calculate_claim", "compare_claim_ratio"}:
                result["category"] = "conditional_calculation"
        return result

    @staticmethod
    def _category(state):
        return ("candidate_defect" if state["failed"] else "evidence_unresolved" if state["blocked"] else "evidence_recorded")

    def _record_requirements(self, submission):
        rule_id = submission["rule_id"]
        rule = next((r for r in self.rules if r["id"] == rule_id), None)
        if not rule:
            return self._error("unknown_or_inactive_rule", details=[{"rule_id": rule_id}])
        expected = {r["id"]: r for r in self.requirements if r["rule_id"] == rule_id and r["active"]}
        observations = submission["observations"]
        seen = set()
        converted = []
        for index, observation in enumerate(observations):
            identity = observation["requirement_id"]
            if identity not in expected or identity in seen:
                return self._error("requirement_id_unknown_inactive_or_duplicate", details=[{
                    "path": f"$.observations[{index}].requirement_id", "active_ids": list(expected)}])
            seen.add(identity)
            requirement = expected[identity]
            status = {"satisfied": "met", "violated": "unmet", "unknown": "unmet", "not_applicable": "not_applicable"}[observation["status"]]
            entry = {"rule_id": rule_id, "status": status, "reason": observation["reason"],
                     "evidence": [{"field": e["source"], "quote": e["quote"]} for e in observation["evidence"]]}
            issue = checklist_evidence_issue([rule], self.candidate, {"checks": [entry]})
            if issue:
                details = citation_diagnostics(observation["evidence"], self.candidate, path=f"$.observations[{index}].evidence")
                return self._error(issue, details=details or [{"path": f"$.observations[{index}]", "requirement": requirement,
                    "message": "Rule evidence must name reasoning or answer. Valid source citations do not substitute for evidence of reasoning in the candidate."}])
            converted.append({"requirement_id": identity, "requirement_quote": requirement["requirement"],
                              **{k: deepcopy(v) for k, v in observation.items() if k != "requirement_id"}})
            if observation["status"] in {"violated", "unknown"}:
                finding = {"rule_id": rule_id, **deepcopy(converted[-1])}
                if finding not in self.findings:
                    self.findings.append(finding)
        if seen != set(expected):
            return self._error("requirement_coverage_missing", details=[{"missing_ids": sorted(set(expected) - seen)}])
        entry = {k: deepcopy(v) for k, v in submission.items() if k != "observations"}
        entry["evidence"] = [{"field": e["source"], "quote": e["quote"]} for e in entry["evidence"]]
        issue = checklist_evidence_issue([rule], self.candidate, {"checks": [entry]})
        if issue:
            return self._error(issue, details=citation_diagnostics(entry["evidence"], self.candidate))
        if rule_id in self.checks:
            # Wording-only re-submission cannot buy a different verdict. Match
            # evidence and statuses, not explanatory prose; keep original bytes.
            same = lambda rows: [(r["requirement_id"], r["status"], r["evidence"]) for r in rows]
            prior = self.checks[rule_id]
            if (prior["status"] == entry["status"] and prior["evidence"] == entry["evidence"]
                    and same(self.observations[rule_id]) == same(converted)):
                return {"ok": True, "reused_committed_evidence": True}
            result = self._error("evidence_already_committed", details=[{"rule_id": rule_id,
                "committed_record": deepcopy(prior), "submitted_record": deepcopy(entry)}])
            result["category"] = "committed_record_conflict"
            result["recovery"]["action"] = "The record is already committed. Do not retry or overwrite it. Work only on pending IDs; a different quotation is not a new obligation."
            return result
        gaps = [o for o in converted if o["status"] in {"violated", "unknown"}]
        if gaps:
            if any(o["status"] == "violated" for o in gaps):
                entry["status"] = "unmet"
            else:
                self.observations[rule_id] = converted
                return {"ok": True, "summary_conflict": submission["status"] != "unmet"}
        elif entry["status"] != "unmet":
            expected_status = "not_applicable" if all(o["status"] == "not_applicable" for o in converted) else "met"
            if entry["status"] != expected_status:
                return self._error("check_observation_conflict")
        self.observations[rule_id] = converted
        return ReasoningInvestigation.execute(self, "record_check", {"check": entry})
