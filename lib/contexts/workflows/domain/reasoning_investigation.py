"""Incremental evidence workspace. Tools choose work; existing validators decide admission."""
from copy import deepcopy

from .reasoning_checklist import checklist_digest, checklist_evidence_issue, checklist_passed
from .reasoning_claims import (claim_anchors, source_passages, verify_claims, verify_calculation,
                              assessment_contract_issue)


class ReasoningInvestigation:
    def __init__(self, rules, context, candidate):
        self.rules = deepcopy(rules)
        self.candidate = deepcopy(candidate)
        self.anchors = claim_anchors(candidate)
        self.passages = source_passages(context)
        self.checks, self.claims = {}, {}
        self.binding = checklist_digest([rules, context, candidate])

    def pending(self):
        return {"rule_ids": [r["id"] for r in self.rules if r["id"] not in self.checks],
                "anchor_ids": [a["id"] for a in self.anchors if a["id"] not in self.claims]}

    def snapshot(self):
        assessment = {"checks": list(self.checks.values()), "claims": list(self.claims.values())}
        rule_issue = checklist_evidence_issue(self.rules, self.candidate, {"checks": assessment["checks"]})
        claims = verify_claims(self.anchors, self.passages, self.candidate, assessment)
        failed = (any(c["status"] == "unmet" for c in assessment["checks"])
                  or any(not row["passed"] and row["status"] != "uncertain" for row in claims["items"]))
        blocked = any(row["status"] == "uncertain" for row in claims["items"])
        return {"assessment": deepcopy(assessment), "pending": self.pending(), "failed": failed, "blocked": blocked,
                "passed": not rule_issue and claims["passed"] and checklist_passed(assessment),
                "rule_issue": rule_issue, "verification": claims}

    def source_index(self):
        # Previews are navigation aids, never substitute evidence for the full passage.
        return [{"id": p["id"], "characters": len(p["text"]),
                 "sha256": checklist_digest(p["text"]), "preview": p["text"][:200],
                 "kind": "dialogue_not_independent_evidence" if p["id"].startswith(("prompt", "question", "messages"))
                         else "provided_source"} for p in self.passages]

    def _source(self, identity):
        return next((p for p in self.passages if p["id"] == identity), None)

    def execute(self, name, arguments):
        """Return recoverable tool feedback; never reinterpret an invalid vote as approval."""
        if not isinstance(arguments, dict):
            return {"ok": False, "issue": "invalid_tool_arguments"}
        if name == "read_source":
            if set(arguments) != {"source_id", "start", "length"}:
                return {"ok": False, "issue": "invalid_source_range"}
            p = self._source(arguments["source_id"])
            start, length = arguments["start"], arguments["length"]
            if (not p or type(start) is not int or type(length) is not int
                    or not 0 <= start < len(p["text"]) or not 1 <= length <= 10000):
                return {"ok": False, "issue": "invalid_source_range"}
            end = min(start + length, len(p["text"]))
            return {"ok": True, "source_id": p["id"], "start": start, "end": end,
                    "total_characters": len(p["text"]), "text": p["text"][start:end],
                    "source_sha256": checklist_digest(p["text"])}
        if name == "search_sources":
            query = arguments.get("query")
            if set(arguments) != {"query"} or not isinstance(query, str) or not 1 <= len(query.strip()) <= 200:
                return {"ok": False, "issue": "invalid_search_query"}
            hits = []
            for p in self.passages:
                at = p["text"].casefold().find(query.casefold())
                if at >= 0:
                    # Offset comes from original text: casefold may change length.
                    import re
                    match = re.search(re.escape(query), p["text"], re.I)
                    if match is None:
                        continue
                    start = max(0, match.start() - 160)
                    hits.append({"source_id": p["id"], "start": start, "text": p["text"][start:start + 600]})
            return {"ok": True, "hits": hits[:8], "more_hits": len(hits) > 8,
                    "scope": "Literal search only; no hit is not proof that a claim is false."}
        if name == "calculate_claim":
            if set(arguments) != {"anchor_id", "calculation", "evidence"}:
                return {"ok": False, "issue": "invalid_calculation_request"}
            anchor = next((a for a in self.anchors if a["id"] == arguments["anchor_id"]), None)
            evidence = arguments["evidence"]
            if not anchor or not isinstance(evidence, list):
                return {"ok": False, "issue": "invalid_calculation_request"}
            sources = {p["id"]: p["text"] for p in self.passages} | self.candidate
            if any(not isinstance(e, dict) or set(e) != {"source", "quote"}
                   or not isinstance(e["source"], str) or not isinstance(e["quote"], str)
                   or not e["quote"] or e["source"] not in sources or e["quote"] not in sources[e["source"]]
                   for e in evidence):
                return {"ok": False, "issue": "claim_unbound_evidence"}
            return {"ok": True, "receipt": verify_calculation(arguments["calculation"],
                anchor["quote"] + "\n" + "\n".join(e["quote"] for e in evidence), anchor["quote"])}
        if name in {"record_check", "record_claim"}:
            field, id_field = ("check", "rule_id") if name == "record_check" else ("claim", "anchor_id")
            entry = arguments.get(field)
            if set(arguments) != {field} or not isinstance(entry, dict) or not isinstance(entry.get(id_field), str):
                return {"ok": False, "issue": "invalid_evidence_submission"}
            identity = entry[id_field]
            saved = self.checks if field == "check" else self.claims
            if identity in saved:
                return {"ok": saved[identity] == entry, "issue": None if saved[identity] == entry else "evidence_already_committed",
                        "pending": self.pending()}
            if field == "check":
                selected = [r for r in self.rules if r["id"] == identity]
                issue = checklist_evidence_issue(selected, self.candidate, {"checks": [entry]})
            else:
                selected = [a for a in self.anchors if a["id"] == identity]
                receipt = verify_claims(selected, self.passages, self.candidate, {"checks": [], "claims": [entry]})
                issue = receipt["issue"] if receipt["issue"] != "claim_not_supported" else None
                if not issue:
                    issue = assessment_contract_issue([], selected, self.passages, self.candidate,
                                                       {"checks": [], "claims": [entry]})
            if issue or not selected:
                return {"ok": False, "issue": issue or "unknown_evidence_id", "pending": self.pending()}
            saved[identity] = deepcopy(entry)
            snapshot = self.snapshot()
            return {"ok": True, "pending": snapshot["pending"], "failed": snapshot["failed"], "blocked": snapshot["blocked"],
                    "passed": snapshot["passed"]}
        if name == "finish_review" and not arguments:
            snapshot = self.snapshot()
            return {"ok": snapshot["passed"] or snapshot["failed"] or snapshot["blocked"], **snapshot}
        return {"ok": False, "issue": "unknown_review_tool"}


def investigation_tools():
    """Tool operations, not a required sequence. Entry schemas are checked by domain validators."""
    def tool(name, description, properties):
        return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties,
                           "required": list(properties), "additionalProperties": False}}}
    text = {"type": "string"}
    return [
        tool("search_sources", "Find literal source passages for a disputed point; no hit is not a negative verdict.", {"query": text}),
        tool("read_source", "Read an exact source range. Retrieve adjacent ranges whenever context or conditions are missing.",
             {"source_id": text, "start": {"type": "integer"}, "length": {"type": "integer"}}),
        tool("calculate_claim", "Get a deterministic typed arithmetic receipt for cited operands. This does not submit a verdict.",
             {"anchor_id": text, "calculation": {"type": "object"}, "evidence": {"type": "array", "items": {"type": "object"}}}),
        tool("record_check", "Commit one complete rule assessment. Invalid entries return feedback; committed votes are immutable.", {"check": {"type": "object"}}),
        tool("record_claim", "Commit one complete claim assessment with citations and scope tests. Negative votes are preserved.", {"claim": {"type": "object"}}),
        tool("finish_review", "Finish when evidence is complete, or a substantive defect is found. Missing work cannot pass.", {}),
    ]


def investigation_feedback(feedback):
    """Send concrete defects to the author, not the entire repeated investigation transcript."""
    if not isinstance(feedback, dict):
        return feedback
    audit = feedback.get("reasoning_checklist")
    if isinstance(audit, dict) and audit.get("version") == 6:
        confirmed = {r["finding_id"] for r in audit.get("resolutions", []) if r["decision"] == "confirmed_defect"}
        return {**{k: deepcopy(v) for k, v in feedback.items() if k != "reasoning_checklist"},
                "reasoning_checklist": {"version": 6, "issue": audit.get("issue"),
                    "candidate_sha256": audit["candidate_sha256"],
                    "deterministic_defects": deepcopy(audit.get("deterministic_defects", [])),
                    "findings": [deepcopy(f) for f in audit.get("findings", []) if f["id"] in confirmed],
                    "resolutions": [deepcopy(r) for r in audit.get("resolutions", []) if r["finding_id"] in confirmed],
                    "instruction": "Repair only these evidenced defects. Do not invent conditions, erase reasoning, or rewrite to satisfy dismissed reviewer allegations."}}
    if not isinstance(audit, dict) or audit.get("version") not in (3, 4, 5):
        return feedback
    checks, claims = [], []
    for label in ("self_check", "independent_check"):
        assessment = audit.get(label, {})
        checks.extend(c for c in assessment.get("checks", []) if c["status"] == "unmet")
        # Include supported-but-invalid calculations as well as negative model judgments.
        failed_ids = {c["anchor_id"] for c in audit.get(label + "_verification", {}).get("items", [])
                      if not c["passed"]}
        failed_ids.update(c["anchor_id"] for c in audit.get(label + "_session", {}).get("assertions", {}).values()
                          if c["status"] == "violated")
        claims.extend(c for c in assessment.get("claims", []) if c["anchor_id"] in failed_ids)
    return {**{k: deepcopy(v) for k, v in feedback.items() if k != "reasoning_checklist"},
            "reasoning_checklist": {"version": audit["version"], "issue": audit.get("issue"),
                "candidate_sha256": audit["candidate_sha256"], "failed_checks": deepcopy(checks),
                "failed_claims": deepcopy(claims),
                "findings": [deepcopy(f) for label in ("self_check", "independent_check")
                             for f in audit.get(label + "_session", {}).get("findings", [])],
                **({"assertion_receipts": [deepcopy(r) for label in ("self_check", "independent_check")
                    for r in audit.get(label + "_session", {}).get("assertions", {}).values()
                    if r["status"] != "verified"]} if audit["version"] == 5 else {}),
                "instruction": "Repair only evidenced defects, then recheck the complete candidate. Review opinions are not factual sources."}}
