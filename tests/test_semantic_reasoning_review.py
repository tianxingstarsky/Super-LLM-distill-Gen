"""Material review cannot waive evidence, invent premises or silently edit history."""
from copy import deepcopy
import json

import pytest

from lib.contexts.workflows.domain.semantic_review import SemanticReviewWorkspace, body_sections
from lib.contexts.workflows.domain.reasoning_investigation import investigation_feedback
from lib.contexts.workflows.application.check_reasoning import CheckReasoning
from lib.prompts import get, render


RULES = [{"id": "scope", "requirement": "Keep the conclusion within the stated evidence.", "allow_na": False}]
BODY = {"reasoning": "There is no universal syntactic split. Some instances can be split clearly.\n\nThe choice is a schema convention.",
        "answer": "Use a consistent convention, not a universal grammatical rule."}
CONTEXT = {"source": "The boundary is not strict in many cases."}


def workspace(disputes=None):
    return SemanticReviewWorkspace(RULES, CONTEXT, BODY, phase="final", disputes=disputes)


def review(w, category=None, kind="scope"):
    citation = {"source": "reasoning", "quote": "There is no universal syntactic split."}
    findings = [] if not category else [{"id": "f1", "rule_id": "scope", "category": category, "kind": kind,
        "body": citation, "basis": [{"source": "source", "quote": CONTEXT["source"]}],
        "reason": "A locally separable case was alleged as a counterexample to a universal rule.",
        "impact": "Scope of the split rule.", "missing_premise": "What the source says about every instance." if category == "insufficient_evidence" else ""}]
    status = {None: "met", "presentation": "met", "defect": "unmet", "insufficient_evidence": "uncertain"}[category]
    return {"checks": [{"rule_id": "scope", "status": status, "evidence": [citation], "reason": "Check actual quantifiers using the complete candidate."}],
            "findings": findings, "covered_sections": [s["id"] for s in w.sections]}


def resolution(identity, decision="reviewer_error"):
    return {"finding_id": identity, "decision": decision, "added_premises": [],
            "evidence": [{"source": "reasoning", "quote": "Some instances can be split clearly."}],
            "reason": "The existing qualification already permits local splits; it does not assert that none are separable."}


class Port:
    def __init__(self, responses):
        self.responses, self.requests = iter(responses), []
    def prepare_reasoning_context(self, value): return deepcopy(value)
    def check_reasoning_cancel(self): pass
    def reasoning_progress(self, *args): pass
    def reasoning_tool_turn(self, key, role, messages, tools):
        self.requests.append((role, deepcopy(messages)))
        name, value = next(self.responses)
        return {"role": "assistant", "content": None, "tool_calls": [{"id": str(len(self.requests)), "type": "function",
                "function": {"name": name, "arguments": json.dumps(value)}}]}


def run(reviews, decisions=None):
    turns = [("submit_semantic_review", r) for r in reviews]
    if decisions is not None: turns.append(("resolve_disputes", {"resolutions": decisions}))
    port = Port(turns)
    return CheckReasoning(port, {"version": 6, "rules": RULES}).execute(["test"], CONTEXT, BODY, phase="final"), port


def test_full_text_coverage_does_not_drop_reasoning_or_answer():
    w = workspace()
    assert len(w.sections) == 3
    assert all(s["text"] == BODY[s["field"]][s["start"]:s["start"] + len(s["text"])] for s in body_sections(BODY))
    value = review(w)
    value["covered_sections"].pop()
    assert w.execute("submit_semantic_review", value)["issue"] == "full_body_coverage_missing"
    assert w.candidate == BODY and not w.snapshot()["passed"]


@pytest.mark.parametrize("change,issue", [
    (lambda r: r["checks"][0].update(status="not_applicable"), "required_rule_waived"),
    (lambda r: r["checks"][0]["evidence"][0].update(quote="There is always a split."), "rule_citation_not_in_body"),
    (lambda r: r["checks"].clear(), "rule_coverage_missing"),
    (lambda r: r["checks"][0].update(status="unmet"), "negative_check_requires_specific_finding"),
])
def test_strict_coverage_and_literal_evidence_still_apply(change, issue):
    w = workspace(); value = review(w); change(value)
    assert w.execute("submit_semantic_review", value)["issue"] == issue


def test_arithmetic_error_cannot_be_relabelled_cosmetic_or_overridden():
    w = workspace()
    assert w.validate_review(review(w, "presentation", "arithmetic")) == "material_defect_cannot_be_presentation"
    value = review(w, "defect", "arithmetic"); value["checks"][0]["status"] = "met"
    assert w.validate_review(value) == "finding_cannot_be_overridden_by_positive_check"
    unknown = review(w, "insufficient_evidence"); unknown["findings"][0]["missing_premise"] = ""
    assert w.validate_review(unknown) == "missing_evidence_must_be_named"


def test_harmless_presentation_is_recorded_without_rewriting_candidate():
    audit, port = run([review(workspace(), "presentation", "wording"), review(workspace())])
    assert audit["passed"] and len(port.requests) == 2 and audit["findings"][0]["category"] == "presentation"
    assert "self_check" not in json.loads(port.requests[1][1][0]["content"])


@pytest.mark.parametrize("decision,passed,issue", [
    ("reviewer_error", True, None),
    ("confirmed_defect", False, "reasoning_agent_found_gap"),
    ("insufficient_evidence", False, "reasoning_agent_evidence_unresolved"),
])
def test_dispute_uses_specific_cited_resolution_not_majority_vote(decision, passed, issue):
    audit, port = run([review(workspace()), review(workspace(), "defect")], [resolution("independent_check:f1", decision)])
    assert audit["passed"] is passed and audit["issue"] == issue
    assert len(port.requests) == 3 and audit["independent_check"]["checks"][0]["status"] == "unmet"
    resolved_input = json.loads(port.requests[-1][1][0]["content"])
    assert resolved_input["candidate"] == BODY
    assert len(resolved_input["environment"]["disputes"]["findings"]) == 1
    feedback = investigation_feedback({"reasoning_checklist": audit})
    assert len(feedback["reasoning_checklist"]["findings"]) == (1 if decision == "confirmed_defect" else 0)


def test_resolution_cannot_invent_premises_or_misquote_conditions():
    w = workspace({"findings": [{"id": "f", "kind": "scope"}]})
    value = {"resolutions": [resolution("f")]}
    value["resolutions"][0]["added_premises"] = ["Assume this only refers to the easy cases."]
    assert w.validate_resolution(value) == "resolution_cannot_add_premises"
    value["resolutions"][0]["added_premises"] = []
    value["resolutions"][0]["evidence"][0]["quote"] = "All instances can be split clearly."
    assert w.validate_resolution(value) == "resolution_requires_original_body_evidence"
    assert w.validate_resolution({"resolutions": []}) == "dispute_coverage_missing"


def test_same_invalid_submission_stops_without_buying_endless_retries():
    bad = review(workspace()); bad["checks"][0]["evidence"][0]["quote"] = "invented"
    audit, port = run([bad, bad])
    assert not audit["passed"] and len(port.requests) == 2
    assert audit["self_check_session"]["stop_receipt"]["reason"] == "repeated_operation"


def test_new_prompt_is_versioned_and_old_prompt_frozen():
    assert "submit_semantic_review" in render(get("workflow.cot_check_agent", version="1.4.0"))
    assert "submit_semantic_review" not in render(get("workflow.cot_check_agent", version="1.3.0"))


def test_calculator_cites_operands_and_does_not_decide_cross_sentence_reference():
    w = SemanticReviewWorkspace(RULES, {"source": "26,559 out of 52,445 instances."}, BODY, phase="final")
    args = {"operation": "percent_of", "left": "26559", "right": "52445",
            "evidence": [{"source": "source", "quote": "26,559 out of 52,445 instances."}]}
    receipt = w.execute("calculate_numbers", args)
    assert receipt["value"].startswith("50.6416") and receipt["candidate_verdict"] is None
    assert receipt["semantic_binding"] == "unverified"
    args["left"] = "14"
    assert w.execute("calculate_numbers", args)["issue"] == "uncited_operand"


def test_read_only_reuse_is_cached_and_does_not_abort_other_completed_work():
    w = workspace()
    args = {"query": "boundary"}
    first, second = w.execute("search_sources", args), w.execute("search_sources", args)
    assert first["hits"] == second["hits"] and second["reused_local_receipt"]
    valid = review(w)
    p = Port([("search_sources", args), ("search_sources", args), ("submit_semantic_review", valid),
              ("submit_semantic_review", valid)])
    audit = CheckReasoning(p, {"version": 6, "rules": RULES}).execute(["test"], CONTEXT, BODY, phase="final")
    assert audit["passed"] and len(p.requests) == 4
    inputs = json.loads(p.requests[0][1][0]["content"])
    assert "claim_anchors" not in inputs and inputs["candidate"] == BODY
    assert "inspect_claim" not in [t["function"]["name"] for t in w.tools()]


def test_explicit_numeric_contradiction_still_blocks_without_model_calls():
    p = Port([])
    body = {"reasoning": "覆盖率为50.6%，不到一半。", "answer": "不到一半。"}
    audit = CheckReasoning(p, {"version": 6, "rules": RULES}).execute(["test"], {}, body, phase="final")
    assert not audit["passed"] and not p.requests
    assert audit["deterministic_defects"][0]["receipt"]["issue"] == "comparison_value_conflict"
    assert investigation_feedback({"reasoning_checklist": audit})["reasoning_checklist"]["deterministic_defects"]


def test_distinct_exact_source_locators_allow_submission_within_the_turn_cap():
    queries = ['boundary', 'strict', 'many', 'cases']
    valid = review(workspace())
    p = Port([*(('search_sources', {'query': q}) for q in queries),
              ('submit_semantic_review', valid), ('submit_semantic_review', valid)])
    audit = CheckReasoning(p, {'version': 6, 'rules': RULES}).execute(['test'], CONTEXT, BODY, phase='final')
    assert audit['passed'] and audit['self_check_session']['turns'] == 5


def test_citation_aliases_expand_exactly_without_erasing_negation_or_inventing_ids():
    w = workspace(); value = review(w)
    value['checks'][0]['evidence'] = [{'citation_id': w.sections[0]['id']}]
    original = deepcopy(value)
    receipt = w.execute('submit_semantic_review', value)
    assert receipt['ok'] and value == original
    assert w.submission['checks'][0]['evidence'][0]['quote'] == w.sections[0]['text']
    assert w.submission['checks'][0]['evidence'][0]['quote'].startswith('There is no')
    other = workspace(); bad = review(other)
    bad['checks'][0]['evidence'] = [{'citation_id': 'invented'}]
    assert other.execute('submit_semantic_review', bad)['issue'] == 'unknown_or_conflicting_citation_id'
    alias = review(other); alias['checks'][0]['evidence'][0]['source'] = 'candidate.reasoning'
    assert other.execute('submit_semantic_review', alias)['ok']
