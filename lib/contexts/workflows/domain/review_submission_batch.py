"""Collect independent review entries; only the original full validator can finish.

This is a transport workspace around an evidence-policy-2 review, not another
quality judge. Model-turn checkpoints deterministically reconstruct the buffer.
"""
from copy import deepcopy

from .reasoning_checklist import checklist_digest
from .semantic_review_feedback import schema_feedback


class ReviewSubmissionBatch:
    session_instruction = (
        "Review submission scheduling: cover ALL applicable items in the first submission, in one tool call. "
        "The program retains valid entries. Afterwards submit ONLY the missing/invalid items named in the latest "
        "tool feedback, with empty arrays for completed categories. A negative finding is completed work, "
        "not an omitted item. Do not change verdicts to repair fields. Read/compute only when evidence is missing. "
        "This controls review delivery, not the author's thinking sequence or the substantive quality requirements.")
    def __init__(self, workspace):
        self.workspace = workspace
        self.resolving = workspace.disputes is not None
        self.schema = deepcopy(workspace.tools()[0]["function"]["parameters"])
        self.records = {field: {} for field in self.schema["properties"]}
        self.errors, self.verdicts = {}, {}
        self.attempts = 0
        self.fatal = None
        self.binding = checklist_digest([workspace.binding, "bulk_review_then_targeted_v1"])

    def __getattr__(self, name):
        return getattr(self.workspace, name)

    def tools(self):
        tools = deepcopy(self.workspace.tools())
        tools[0]["function"]["description"] = (
            "First response: submit all applicable checks together in ONE call. "
            "Later responses: submit ONLY missing or invalid entries listed in environment.batch_submission. "
            "Use empty arrays for completed categories. Valid entries are retained, including negative findings. "
            "Do not change a verdict to repair its representation. Complete evidence and existing validation are required. "
            "Use read/calculation tools only for a specific missing fact; no per-item read ritual.")
        return tools

    def _identity(self, field, entry):
        if field == "covered_sections":
            return entry if isinstance(entry, str) else None
        name = {"checks": "rule_id", "findings": "id", "risk_checks": "id", "resolutions": "finding_id"}[field]
        value = entry.get(name) if isinstance(entry, dict) else None
        return value if isinstance(value, str) and value else None

    def _expected(self):
        if self.resolving:
            return {"resolutions": [f["id"] for f in self.disputes["findings"]]}
        return {"checks": [r["id"] for r in self.rules],
                "covered_sections": [s["id"] for s in self.sections],
                "risk_checks": [o["id"] for o in self.obligations]}

    def _assembled(self):
        return {field: deepcopy(list(entries.values())) for field, entries in self.records.items()}

    def pending(self):
        missing = {field: [identity for identity in ids if identity not in self.records[field]]
                   for field, ids in self._expected().items()}
        return {"rule_ids": missing.get("checks", []), "anchor_ids": missing.get("covered_sections", []),
                "risk_ids": missing.get("risk_checks", []), "finding_ids": missing.get("resolutions", []),
                "invalid_items": list(self.errors)}

    def environment(self, *, role, remaining_turns):
        return {**self.workspace.environment(role=role, remaining_turns=remaining_turns),
                "batch_submission": {
                    "version": 1, "mode": "all_items" if self.attempts == 0 else "missing_or_invalid_only",
                    "pending": self.pending(), "errors": deepcopy(list(self.errors.values())),
                    "accepted_ids": {field: list(entries) for field, entries in self.records.items()},
                    "instruction": "First submit all applicable items in one response. Then only fill the listed gaps; "
                        "do not resubmit or rescore accepted items. Negative decisions are valid completed work. "
                        "Do not invent evidence or change a verdict to fix a field. Keep all reasoning unchanged.",
                    "completion": "The program merges entries and reruns the unchanged full validator. "
                        "A complete review may still reject the candidate; unknown is not pass."}}

    def snapshot(self):
        snapshot = self.workspace.snapshot()
        if self.workspace.submission is None:
            snapshot.update(assessment=self._assembled(), pending=self.pending(),
                            findings=deepcopy(list(self.records.get("findings", {}).values())))
        if self.fatal or self.errors:
            snapshot["passed"] = False
            snapshot["verification"] = {"passed": False, "items": [], "issue": self.fatal or "review_batch_incomplete"}
        return {**snapshot, "batch_submission_version": 1,
                "blocked": bool(self.fatal), "protocol_blocker": self.fatal,
                "entry_errors": deepcopy(list(self.errors.values()))}

    def _entry_issue(self, field, entry):
        if details := schema_feedback(self.schema["properties"][field]["items"], entry):
            return "invalid_tool_arguments", details
        value = {name: [] for name in self.records}
        value[field] = [entry]
        issue = (self.workspace.validate_resolution_entries(value) if self.resolving
                 else self.workspace.validate_review_entries(value))
        return issue, []

    def execute(self, name, arguments):
        expected_tool = "resolve_disputes" if self.resolving else "submit_semantic_review"
        if name != expected_tool:
            return self.workspace.execute(name, arguments)
        self.attempts += 1
        if not isinstance(arguments, dict) or set(arguments) - set(self.records):
            return self._error("review_batch_fields", details=[{
                "path": "$", "message": "Submit only the documented arrays; omitted arrays remain pending."}])
        entries = []
        expected = self._expected()
        # A batch is an envelope. Each item gets its own citation/schema check,
        # so an unknown citation in one item cannot discard its valid siblings.
        for field in self.records:
            values = arguments.get(field, [])
            if not isinstance(values, list):
                self.errors[field] = {"target": field, "path": field, "message": "Expected an array."}
                continue
            if field in arguments:
                self.errors.pop(field, None)
            identities = [self._identity(field, item) for item in values]
            for index, raw in enumerate(values):
                path = f"{field}[{index}]"
                identity = identities[index]
                target = f"{field}:{identity}" if identity else path
                if not identity or field in expected and identity not in expected[field]:
                    # An unaddressable finding cannot safely disappear on a
                    # later empty submission. Preserve it as unresolved.
                    issue, details = "review_entry_identity_invalid", []
                    self.fatal = issue
                    entry = deepcopy(raw)
                else:
                    entry, repairs, details = self.workspace._resolve_citations(raw)
                    issue = "unknown_or_conflicting_citation_id" if details else None
                    if not issue:
                        issue, details = self._entry_issue(field, entry)
                    if identities.count(identity) > 1:
                        issue = "duplicate_review_entry"
                    vote = entry.get("status", entry.get("decision", entry.get("category"))) if isinstance(entry, dict) else None
                    negative = isinstance(vote, str) and vote in {"unmet", "uncertain", "defect", "insufficient_evidence", "confirmed_defect"}
                    if target in self.verdicts and vote != self.verdicts[target]:
                        issue = "review_verdict_cannot_be_replaced"
                    elif negative:
                        self.verdicts[target] = vote
                    if identity in self.records[field]:
                        if entry == self.records[field][identity] and not issue:
                            entries.append({"path": path, "target": target,
                                            "result": {"ok": True, "reused_committed_evidence": True}})
                            continue
                        issue = "committed_record_conflict"
                        self.fatal = issue
                if issue:
                    detail = {"target": target, "path": path, "message": issue, "details": details,
                              "instruction": "Repair only this entry using actual evidence. Preserve its verdict."}
                    self.errors[target] = detail
                    result = self._error(issue, details=[detail])
                else:
                    self.records[field][identity] = deepcopy(entry)
                    self.errors.pop(target, None)
                    result = {"ok": True, "committed": True}
                entries.append({"path": path, "target": target, "result": result})
        # Partial validity never authorizes a candidate. This exact validator is
        # also used by the legacy whole-table path and by cached-role receipts.
        merged = self._assembled()
        final_issue = (self.workspace.validate_resolution(merged) if self.resolving
                       else self.workspace.validate_review(merged))
        self.errors.pop("relationships", None)
        missing = any(self.pending()[field] for field in ("rule_ids", "anchor_ids", "risk_ids", "finding_ids"))
        if final_issue and not missing:
            missing_link = final_issue in {"negative_check_requires_specific_finding", "negative_risk_requires_finding"}
            self.errors["relationships"] = {"target": "relationships", "path": "$", "message": final_issue,
                "instruction": ("Supply the missing finding linked to the existing negative check/risk. Never turn it into a pass."
                    if missing_link else "Committed review entries conflict. Retain both decisions for separate review; do not buy another format repair.")}
            if not missing_link:
                self.fatal = "committed_review_inconsistent"
        if not final_issue and not self.errors and not self.fatal:
            self.workspace.submission = merged
        return {"ok": all(item["result"]["ok"] for item in entries) and not self.errors and not self.fatal,
                "entries": entries, "pending": self.pending(), "complete": self.snapshot()["passed"],
                "issue": final_issue if self.errors else None,
                "reused_committed_evidence": bool(entries) and all(
                    item["result"].get("reused_committed_evidence") for item in entries),
                "candidate_changed": False}
