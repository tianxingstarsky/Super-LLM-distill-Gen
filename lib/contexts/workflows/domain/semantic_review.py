"""Version 6 material review: full-text coverage, cited findings and bounded disputes.

This contract checks evidence identity, not semantic truth. Reviewer conclusions
remain model judgments. No majority vote or missing evidence can create a fact.
"""
from copy import deepcopy
from decimal import Decimal, localcontext
import re

from .reasoning_checklist import checklist_digest
from .reasoning_environment import compile_requirements
from .reasoning_evidence import ReasoningEvidence
from .reasoning_investigation import ReasoningInvestigation
from .reasoning_assertions import assertion_anchors, assertion_receipt
from .reasoning_claims import DECIMAL, _number_present


POLICY = {
    "version": 3,
    "relaxed": ["No fixed reasoning order, minimum length or rhetorical self-question requirement.",
                "Do not demand a proof of every routine inference or a separate verdict for every numeral.",
                "A harmless paraphrase or omitted routine explanation is a presentation note, not a factual defect."],
    "retained": ["Every active rule and the complete reasoning/answer must be reviewed.",
                 "Wrong facts, arithmetic, units, changed quantifiers, hidden premises and unsupported decisive conclusions remain blocking.",
                 "Uncertain evidence stays uncertain; do not rewrite a candidate to satisfy an invented premise.",
                 "Custom requirements are not waived. Literal quotations and tool claims must be truthful."],
    "scope": "Evaluate the complete actual proposition with existing qualifications. A local separable example cannot refute 'no universal splitting rule'. An inexact quotation may be a local defect without invalidating the conclusion; identify its actual impact.",
    "speech_act": "Distinguish a term, bilingual gloss or conceptual paraphrase from an attributed verbatim source quotation. Quotation marks alone do not assert verbatim provenance. Require literal source wording only when the candidate explicitly claims it or a user rule requires it. Paraphrases must preserve material meaning and scope; use qualifications already present in the complete candidate, never invented ones. This does not waive any custom rule.",
}


def body_sections(candidate):
    sections = []
    for field, text in candidate.items():
        for match in re.finditer(r"\S(?:.*?\S)?(?=\n\s*\n|\Z)", text, re.S):
            sections.append({"id": f"{field}:{match.start()}", "field": field,
                             "start": match.start(), "text": match[0]})
    return sections


def _object(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def _array(items):
    return {"type": "array", "items": items}


TEXT = {"type": "string"}
CITATION = {"oneOf": [_object({"source": TEXT, "quote": TEXT}), _object({"citation_id": TEXT})],
            "description": "Use a provided body section ID as citation_id to cite its exact text without retyping; otherwise source/quote must match literally."}


def semantic_tools(*, resolving=False):
    def tool(name, description, schema):
        return {"type": "function", "function": {"name": name, "description": description, "parameters": schema}}
    checks = _object({"rule_id": TEXT, "status": {"enum": ["met", "unmet", "uncertain", "not_applicable"], "type": "string"},
                      "evidence": _array(CITATION), "reason": TEXT})
    finding = _object({"id": TEXT, "rule_id": TEXT,
        "category": {"type": "string", "enum": ["defect", "insufficient_evidence", "presentation"]},
        "kind": {"type": "string", "enum": ["fact", "arithmetic", "units", "scope", "argument", "task", "provenance", "wording", "exposition"]},
        "body": CITATION, "basis": _array(CITATION), "reason": TEXT, "impact": TEXT, "missing_premise": TEXT})
    submission = tool("submit_semantic_review", "Commit one full review. Cover every section and active rule. Only findings need detailed evidence; do not create a claim form for every number.",
        _object({"covered_sections": _array(TEXT), "checks": _array(checks), "findings": _array(finding)}))
    resolution = tool("resolve_disputes", "Resolve each frozen disputed finding exactly once. Keep both prior reviews; do not add premises or rewrite the candidate.",
        _object({"resolutions": _array(_object({"finding_id": TEXT,
            "decision": {"type": "string", "enum": ["confirmed_defect", "insufficient_evidence", "reviewer_error", "presentation_only"]},
            "evidence": _array(CITATION), "reason": TEXT, "added_premises": _array(TEXT)}))}))
    return [resolution if resolving else submission,
        tool("search_sources", "Find literal source text; no hit is not proof of absence.", _object({"query": TEXT})),
        tool("read_source", "Read adjacent source context if needed.", _object({"source_id": TEXT, "start": {"type": "integer"}, "length": {"type": "integer"}})),
        tool("calculate_numbers", "Exact Decimal arithmetic on literally cited operands. For a proportion use left=part, right=total and percent_of. It does not decide semantic binding or truth.",
            _object({"operation": {"type": "string", "enum": ["difference", "ratio", "percent_of", "sum"]},
                     "left": TEXT, "right": TEXT, "evidence": _array(CITATION)})),
        ]


class SemanticReviewWorkspace(ReasoningEvidence):
    """New explicit protocol; historical per-claim contracts are untouched."""
    max_steps = 6
    format_recovery_tools = {"submit_semantic_review", "resolve_disputes"}
    include_claim_inventory = False
    repeatable_read_tools = {"read_source", "search_sources", "calculate_numbers"}
    progress_read_tools = {"read_source", "search_sources"}

    def __init__(self, rules, context, candidate, *, phase, disputes=None):
        requirements = [r for r in compile_requirements(rules, phase) if r["active"]]
        active = {r["rule_id"] for r in requirements}
        effective = [{"id": r["id"], "requirement": "；".join(x["requirement"] for x in requirements if x["rule_id"] == r["id"]),
                      "allow_na": r["allow_na"]} for r in rules if r["id"] in active]
        super().__init__(effective, context, candidate)
        self.phase, self.sections, self.requirements = phase, body_sections(candidate), requirements
        self.anchors = assertion_anchors(candidate)
        self.disputes = deepcopy(disputes)
        self.submission = None
        self.read_receipts = {}
        self.binding = checklist_digest([6, POLICY, phase, rules, context, candidate, disputes])

    def tools(self):
        return semantic_tools(resolving=self.disputes is not None)

    def _error(self, issue, *, details=None, **extra):
        return {**super()._error(issue, details=details, **extra), "category": "tool_input_error", "candidate_changed": False}

    def environment(self, *, role, remaining_turns):
        return {"protocol_version": 6, "policy": POLICY, "phase": self.phase, "role": role,
                "requirements": self.rules, "sections": [{**{k: v for k, v in s.items() if k != "text"}, "preview": s["text"][:160]} for s in self.sections],
                "submission_contract": {"rule_ids": [r["id"] for r in self.rules],
                    "rule_evidence": "Use these exact rule IDs, not subclause IDs. Prefer {citation_id: section_id} for body evidence; it expands to the exact original paragraph. Every applicable check needs body evidence; additional literal source/task citations are welcome.",
                    "finding_evidence": "body is a literal reasoning/answer citation; basis may cite any provided source or body. No paraphrased quotations."},
                "disputes": self.disputes, "remaining_turns": remaining_turns,
                "permissions": {"edit_candidate": False, "add_premises": False, "waive_rules": False},
                "completion": "One complete evidence-backed review; a tool representation error is not a candidate defect."}

    def pending(self):
        return {"rule_ids": [] if self.submission else [r["id"] for r in self.rules],
                "anchor_ids": [] if self.submission else [s["id"] for s in self.sections]}

    def snapshot(self):
        review = self.submission or {"checks": [], "findings": [], "covered_sections": []}
        findings = review.get("findings", [])
        # Session completion is separate from the final admission decision.
        return {"assessment": deepcopy(review), "pending": self.pending(), "failed": False, "blocked": False,
                "passed": self.submission is not None, "findings": deepcopy(findings),
                "verification": {"passed": self.submission is not None, "items": [], "issue": None},
                "material_defects": any(f["category"] == "defect" for f in findings),
                "unresolved": any(f["category"] == "insufficient_evidence" for f in findings)}

    def _citations_valid(self, citations, *, body=False, required=True):
        texts = self.candidate if body else {p["id"]: p["text"] for p in self.passages} | self.candidate
        return (isinstance(citations, list) and (bool(citations) or not required)
                and all(isinstance(c, dict) and set(c) == {"source", "quote"}
                        and isinstance(c["source"], str) and c["source"] in texts
                        and isinstance(c["quote"], str) and bool(c["quote"].strip())
                        and c["quote"] in texts[c["source"]] for c in citations))

    def validate_review(self, review):
        return self._validate_review(review, complete=True)

    def validate_review_entries(self, review):
        """Validate submitted entries only; never a completion/admission verdict."""
        return self._validate_review(review, complete=False)

    def _validate_review(self, review, *, complete):
        if not isinstance(review, dict) or set(review) != {"checks", "findings", "covered_sections"}:
            return "review_fields"
        if (not all(isinstance(review[k], list) for k in review)
                or len(review["findings"]) > 64 or len(review["checks"]) > getattr(self, "max_checks", 32)):
            return "review_lists"
        covered = review["covered_sections"]
        if (any(not isinstance(s, str) for s in covered) or len(covered) != len(set(covered))
                or not set(covered).issubset({s["id"] for s in self.sections})
                or complete and set(covered) != {s["id"] for s in self.sections}):
            return "full_body_coverage_missing"
        expected, seen = {r["id"]: r for r in self.rules}, set()
        for c in review["checks"]:
            if (not isinstance(c, dict) or set(c) != {"rule_id", "status", "evidence", "reason"}
                    or not isinstance(c["rule_id"], str) or c["rule_id"] not in expected or c["rule_id"] in seen
                    or c["status"] not in ("met", "unmet", "uncertain", "not_applicable")
                    or not isinstance(c["reason"], str) or not c["reason"].strip()):
                return "invalid_rule_review"
            seen.add(c["rule_id"])
            if c["status"] == "not_applicable" and not expected[c["rule_id"]]["allow_na"]:
                return "required_rule_waived"
            if (not self._citations_valid(c["evidence"], required=c["status"] != "not_applicable")
                    or c["status"] != "not_applicable" and not any(e["source"] in self.candidate for e in c["evidence"])):
                return "rule_citation_not_in_body"
        if complete and seen != set(expected):
            return "rule_coverage_missing"
        ids = set()
        for f in review["findings"]:
            if (not isinstance(f, dict) or set(f) != {"id", "rule_id", "category", "kind", "body", "basis", "reason", "impact", "missing_premise"}
                    or not all(isinstance(f[k], str) for k in ("id", "rule_id", "category", "kind", "reason", "impact", "missing_premise"))
                    or not f["id"] or f["id"] in ids or f["rule_id"] not in expected
                    or f["category"] not in ("defect", "insufficient_evidence", "presentation")
                    or f["kind"] not in ("fact", "arithmetic", "units", "scope", "argument", "task", "provenance", "wording", "exposition")
                    or not f["reason"].strip() or not f["impact"].strip()):
                return "invalid_finding"
            ids.add(f["id"])
            if not self._citations_valid([f["body"]], body=True) or not self._citations_valid(f["basis"]):
                return "finding_unbound_evidence"
            if f["category"] == "presentation" and f["kind"] not in ("wording", "exposition"):
                return "material_defect_cannot_be_presentation"
            if f["category"] == "insufficient_evidence" and not f["missing_premise"].strip():
                return "missing_evidence_must_be_named"
            status = next((c["status"] for c in review["checks"] if c["rule_id"] == f["rule_id"]), None)
            if (complete or status is not None) and (f["category"] == "defect" and status != "unmet"
                    or f["category"] == "insufficient_evidence" and status not in ("unmet", "uncertain")):
                return "finding_cannot_be_overridden_by_positive_check"
        for c in review["checks"]:
            if complete and c["status"] in ("unmet", "uncertain") and not any(f["rule_id"] == c["rule_id"] and
                    f["category"] == ("defect" if c["status"] == "unmet" else "insufficient_evidence") for f in review["findings"]):
                return "negative_check_requires_specific_finding"
        return None

    def validate_resolution(self, value):
        return self._validate_resolution(value, complete=True)

    def validate_resolution_entries(self, value):
        return self._validate_resolution(value, complete=False)

    def _validate_resolution(self, value, *, complete):
        if not isinstance(value, dict) or set(value) != {"resolutions"} or not isinstance(value["resolutions"], list):
            return "resolution_fields"
        expected = {f["id"]: f for f in self.disputes["findings"]}
        seen = set()
        for r in value["resolutions"]:
            if (not isinstance(r, dict) or set(r) != {"finding_id", "decision", "evidence", "reason", "added_premises"}
                    or not isinstance(r["finding_id"], str) or r["finding_id"] not in expected or r["finding_id"] in seen
                    or r["decision"] not in ("confirmed_defect", "insufficient_evidence", "reviewer_error", "presentation_only")
                    or not isinstance(r["reason"], str) or not r["reason"].strip()):
                return "invalid_resolution"
            seen.add(r["finding_id"])
            if r["added_premises"] != []:
                return "resolution_cannot_add_premises"
            if not self._citations_valid(r["evidence"]) or not any(c["source"] in self.candidate for c in r["evidence"]):
                return "resolution_requires_original_body_evidence"
            if r["decision"] == "presentation_only" and expected[r["finding_id"]]["kind"] not in ("wording", "exposition"):
                return "material_defect_cannot_be_presentation"
        return None if not complete or seen == set(expected) else "dispute_coverage_missing"

    def execute(self, name, arguments):
        arguments, repairs, errors = self._resolve_citations(arguments)
        if errors:
            return self._error("unknown_or_conflicting_citation_id", details=errors)
        if name in self.repeatable_read_tools:
            identity = checklist_digest([name, arguments])
            if identity in self.read_receipts:
                return {**deepcopy(self.read_receipts[identity]), "reused_local_receipt": identity, "input_repairs": repairs}
            receipt = self._execute(name, arguments)
            if receipt.get("ok"):
                self.read_receipts[identity] = deepcopy(receipt)
            return {**receipt, "input_repairs": repairs}
        return {**self._execute(name, arguments), "input_repairs": repairs}

    def _resolve_citations(self, arguments):
        registry = {s["id"]: {"source": s["field"], "quote": s["text"]} for s in self.sections}
        repairs, errors = [], []
        def visit(value, path):
            if isinstance(value, list):
                return [visit(v, f"{path}[{i}]") for i, v in enumerate(value)]
            if not isinstance(value, dict):
                return value
            if "citation_id" in value:
                identity = value["citation_id"]
                if set(value) != {"citation_id"} or not isinstance(identity, str) or identity not in registry:
                    errors.append({"path": path, "message": "Use one registered section ID or a source/quote; no invented or conflicting citation."})
                    return deepcopy(value)
                repairs.append({"path": path, "citation_id": identity, "operation": "expand_exact_body_section"})
                return deepcopy(registry[identity])
            if set(value) == {"source", "quote"} and value["source"] in ("candidate.reasoning", "candidate.answer"):
                field = value["source"].split(".")[1]
                if isinstance(value["quote"], str) and value["quote"] in self.candidate.get(field, ""):
                    repairs.append({"path": path, "operation": "explicit_candidate_field_alias", "quote_unchanged": True})
                    return {"source": field, "quote": value["quote"]}
            return {k: visit(v, path + "." + k) for k, v in value.items()}
        return visit(arguments, "$"), repairs, errors

    def _execute(self, name, arguments):
        expected_tool = "resolve_disputes" if self.disputes is not None else "submit_semantic_review"
        if name == expected_tool:
            if self.submission is not None:
                return {"ok": arguments == self.submission, "reused_committed_evidence": True,
                        "issue": None if arguments == self.submission else "committed_record_conflict"}
            issue = self.validate_resolution(arguments) if self.disputes is not None else self.validate_review(arguments)
            if issue:
                from .reasoning_references import citation_diagnostics
                citations = []
                if isinstance(arguments, dict):
                    for field in ("checks", "resolutions"):
                        for entry in arguments.get(field, []) if isinstance(arguments.get(field, []), list) else []:
                            if isinstance(entry, dict) and isinstance(entry.get("evidence"), list):
                                citations.extend(entry["evidence"])
                    for f in arguments.get("findings", []) if isinstance(arguments.get("findings", []), list) else []:
                        if isinstance(f, dict):
                            citations.extend([f.get("body")] + (f.get("basis") if isinstance(f.get("basis"), list) else []))
                return self._error(issue, details=[{"path": "$", "message": issue,
                    "expected_rule_ids": [r["id"] for r in self.rules],
                    "citation_diagnostics": citation_diagnostics([c for c in citations if isinstance(c, dict)],
                        {p["id"]: p["text"] for p in self.passages} | self.candidate)}])
            self.submission = deepcopy(arguments)
            return {"ok": True, "category": "evidence_recorded", "pending": self.pending()}
        if name in ("read_source", "search_sources"):
            return ReasoningInvestigation.execute(self, name, arguments)
        if name == "calculate_numbers":
            if (not isinstance(arguments, dict) or set(arguments) != {"operation", "left", "right", "evidence"}
                    or arguments["operation"] not in ("difference", "ratio", "percent_of", "sum")
                    or any(not isinstance(arguments[k], str) or not DECIMAL.fullmatch(arguments[k]) for k in ("left", "right"))
                    or not self._citations_valid(arguments["evidence"])):
                from .reasoning_references import citation_diagnostics
                citations = arguments.get("evidence", []) if isinstance(arguments, dict) else []
                return self._error("invalid_cited_calculation", details=[{"path": "$",
                    "message": "Use operation difference/ratio/percent_of/sum, decimal strings left/right, and exact citations. Body source IDs are reasoning and answer.",
                    "citations": citation_diagnostics([c for c in citations if isinstance(c, dict)],
                        {p["id"]: p["text"] for p in self.passages} | self.candidate) if isinstance(citations, list) else []}])
            evidence_text = "\n".join(c["quote"] for c in arguments["evidence"])
            if not all(_number_present(arguments[k], evidence_text) for k in ("left", "right")):
                return self._error("uncited_operand")
            left, right = Decimal(arguments["left"]), Decimal(arguments["right"])
            op = arguments["operation"]
            if op in ("ratio", "percent_of") and right == 0:
                return self._error("division_by_zero")
            with localcontext() as ctx:
                ctx.prec = 60
                number = left - right if op == "difference" else left + right if op == "sum" else left / right * (100 if op == "percent_of" else 1)
            return {"ok": True, "category": "conditional_calculation", "value": str(number),
                    "operation": op, "left": arguments["left"], "right": arguments["right"],
                    "semantic_binding": "unverified", "candidate_verdict": None}
        return self._error("unknown_review_tool")


def explicit_comparison_defects(candidate):
    """Retain the previous hard check only for its unambiguous supported grammar."""
    defects = []
    for anchor in assertion_anchors(candidate):
        receipt = assertion_receipt(anchor, {"status": "supported"}, {"stance": "asserted"})
        if receipt and receipt["status"] == "violated":
            defects.append({"anchor": anchor, "receipt": receipt})
    return defects
