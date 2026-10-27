"""Explicit source scope and reusable proofs for an opt-in semantic review policy."""
from copy import deepcopy

from .semantic_review import SemanticReviewWorkspace
from .reasoning_checklist import checklist_digest
from .review_source_inventory import source_inventory, reusable_prefix


class EvidenceScopedReview(SemanticReviewWorkspace):
    stable_source_prefix = True
    max_checks = 35

    def __init__(self, rules, context, candidate, *, phase, disputes=None):
        super().__init__(rules, context, candidate, phase=phase, disputes=disputes)
        self.inventory = source_inventory(context)
        self.source_citations = {}
        self.prefix = reusable_prefix(context.get("verified_prefix_review"), context, candidate, rules) if phase == "final" else None
        # Do not expose metadata fields as factual passages or duplicate numbered
        # and plain text. Prompt/history remains available as task evidence.
        self.passages = [p for p in self.passages if not p["id"].startswith(("source.coverage_manifest", "source.source_name"))]
        self.binding = checklist_digest([self.binding, "evidence_policy", 1, self.inventory, self.prefix,
                                        "precise_review_feedback_v3", "original_source_spans_v1"])

    def tools(self):
        tools = deepcopy(super().tools())
        submission = tools[0]["function"]
        if submission["name"] == "resolve_disputes":
            fields = submission["parameters"]["properties"]["resolutions"]["items"]["properties"]
            fields["finding_id"] = {**fields["finding_id"], "enum": [f["id"] for f in self.disputes["findings"]]}
            submission["description"] += " Use each exact frozen finding_id including its reviewer prefix. Do not use the shorter ID in the nested original reviews."
        if submission["name"] == "submit_semantic_review":
            submission["description"] = (
                "Commit one full review. Cover every section and active rule. Every applicable rule, "
                "including evidence_scope, needs a citation to the actual candidate proposition in reasoning/answer. "
                "Keep external source citations as additional basis; they cannot replace candidate evidence. "
                "Findings additionally identify a concrete defect, uncertainty or presentation issue.")
            checks = submission["parameters"]["properties"]["checks"]["items"]["properties"]
            checks["rule_id"] = {**checks["rule_id"], "enum": [r["id"] for r in self.rules]}
            checks["evidence"]["description"] = (
                "Cite at least one candidate reasoning/answer section ID for every applicable check. "
                "Also cite literal source text when checking factual support. Never invent a quotation.")
        for tool in tools:
            name = tool["function"]["name"]
            if name == "search_sources":
                tool["function"]["description"] = (
                    "Locate original source spans across PDF whitespace and line-end hyphenation. "
                    "Use the returned citation_id to quote the exact original match, or context_citation_id "
                    "for its full surrounding window. Navigation matches do not prove truth or entailment.")
            elif name == "read_source":
                tool["function"]["description"] += (
                    " The returned citation_id expands to the exact requested original span, preserving line breaks.")
        return tools

    def _register_source_span(self, source_id, start, end):
        source = self._source(source_id)
        quote = source["text"][start:end]
        identity = "source-span:" + checklist_digest([source_id, start, end, quote])
        self.source_citations[identity] = {"source": source_id, "quote": quote}
        return identity

    def _resolve_citations(self, arguments):
        repairs = []
        def expand(value, path):
            if isinstance(value, list):
                return [expand(v, f"{path}[{i}]") for i, v in enumerate(value)]
            if not isinstance(value, dict):
                return value
            identity = value.get("citation_id")
            if set(value) == {"citation_id"} and isinstance(identity, str) and identity in self.source_citations:
                repairs.append({"path": path, "citation_id": identity, "operation": "expand_exact_source_span"})
                return deepcopy(self.source_citations[identity])
            return {k: expand(v, path + "." + k) for k, v in value.items()}
        resolved, body_repairs, errors = super()._resolve_citations(expand(arguments, "$"))
        return resolved, repairs + body_repairs, errors

    def _execute(self, name, arguments):
        expected = "resolve_disputes" if self.disputes is not None else "submit_semantic_review"
        if name == expected and self.submission is None:
            from .semantic_review_feedback import schema_feedback
            if details := schema_feedback(self.tools()[0]["function"]["parameters"], arguments):
                if self.disputes is None:
                    from .semantic_review_feedback import review_feedback
                    details += review_feedback(self, arguments)
                issue = self.validate_resolution(arguments) if self.disputes is not None else self.validate_review(arguments)
                return self._error(issue or "invalid_tool_arguments", details=details)
        if name == "search_sources" and isinstance(arguments, dict):
            query = arguments.get("query")
            if set(arguments) == {"query"} and isinstance(query, str) and 1 <= len(query.strip()) <= 200:
                from .source_span_navigation import search_original_spans
                hits, more = search_original_spans(self.passages, query)
                for hit in hits:
                    hit["citation_id"] = self._register_source_span(hit["source_id"], hit["match_start"], hit["match_end"])
                    hit["context_citation_id"] = self._register_source_span(hit["source_id"], hit["start"], hit["end"])
                return {"ok": True, "hits": hits, "more_hits": more,
                        "scope": "Layout-tolerant navigation only. Citation IDs retain exact original text. No hit cannot prove absence; a hit cannot prove entailment."}
        if name == "read_source":
            receipt = super()._execute(name, arguments)
            if receipt.get("ok"):
                receipt["citation_id"] = self._register_source_span(receipt["source_id"], receipt["start"], receipt["end"])
            return receipt
        if name == "submit_semantic_review" and self.disputes is None and self.submission is None:
            from .semantic_review_feedback import review_feedback
            if issue := self.validate_review(arguments):
                details = review_feedback(self, arguments)
                if details:
                    return self._error(issue, details=details)
        return super()._execute(name, arguments)

    def initial_sources(self, *, max_characters=260000):
        return super().initial_sources(max_characters=max_characters)

    def environment(self, *, role, remaining_turns):
        environment = super().environment(role=role, remaining_turns=remaining_turns)
        return {**environment, "evidence_policy": 1, "source_inventory": self.inventory,
                "verified_prefix": self.prefix,
                "source_citation_contract": "search_sources/read_source return registered citation_id values for exact original source spans. Use them in evidence or finding basis to preserve PDF line breaks without retyping. A source citation never substitutes for the candidate body citation. Unknown IDs remain invalid.",
                "review_scope": "Full current reasoning and answer, including consistency with actual conversation history. History is not independent evidence.",
                "source_absence": "No search hit cannot prove absence, even with all text pages. Inspect relevant methods/appendices; no coverage declaration guarantees extraction accuracy."}

    def validate_resolution(self, value):
        return self._validate_resolution(value, complete=True)

    def validate_resolution_entries(self, value):
        return self._validate_resolution(value, complete=False)

    def _validate_resolution(self, value, *, complete):
        issue = super()._validate_resolution(value, complete=complete)
        if issue:
            return issue
        # Scope/provenance/factual disputes cannot be dismissed by citing only
        # the candidate's own assertion. Keep uncertainty if no external basis.
        findings = {f["id"]: f for f in self.disputes["findings"]}
        for decision in value["resolutions"]:
            finding = findings[decision["finding_id"]]
            if (decision["decision"] == "reviewer_error" and finding["kind"] in ("fact", "provenance")
                    and not any(c["source"] not in self.candidate for c in decision["evidence"])):
                return "factual_dismissal_requires_external_evidence"
        return None
