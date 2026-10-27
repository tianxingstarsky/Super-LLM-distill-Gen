"""Reference evidence must help recovery without inventing semantic entailment."""
from copy import deepcopy
import json
from pathlib import Path
import pytest

from lib.contexts.workflows.domain.reasoning_workspace import ReasoningWorkspace
from lib.contexts.workflows.domain.reasoning_evidence import ReasoningEvidence
from test_reasoning_environment import claim_entry, check_entry


TEXT = "52,445 条任务中 26,559 条能提取结构。高频组合占14%。该结构特征覆盖不到一半任务。"


def workspace(text=TEXT):
    return ReasoningWorkspace([], {}, {"reasoning": text}, phase="prefix")


def arguments(w, numerator="26559", denominator="52445", citation=TEXT):
    return {"anchor_id": w.anchors[-1]["id"],
            "numerator": {"source": "reasoning", "quote": citation, "value": numerator},
            "denominator": {"source": "reasoning", "quote": citation, "value": denominator}}


def test_reference_context_retains_competing_values_without_selecting_nearest():
    w = workspace()
    result = w.execute("inspect_claim", {"anchor_id": w.anchors[-1]["id"]})
    assert result["text"] == TEXT
    assert [v["quote"] for v in result["quantities"]] == ["52,445 ", "26,559 ", "14%"]
    assert w.candidate["reasoning"] == TEXT
    assert not w.claims and not w.snapshot()["passed"]


@pytest.mark.parametrize("numerator,denominator,holds", [("26559", "52445", False), ("14", "100", True)])
def test_arithmetic_is_conditional_and_does_not_resolve_antecedent(numerator, denominator, holds):
    w = workspace(TEXT + "另有100个案例，14个命中。")
    args = arguments(w, numerator, denominator, w.candidate["reasoning"])
    args["anchor_id"] = next(a["id"] for a in w.anchors if "不到一半" in a["quote"])
    result = w.execute("compare_claim_ratio", args)
    assert result["ok"] and result["comparison_holds"] == holds
    assert result["candidate_verdict"] is None and result["semantic_binding"] == "unverified"
    assert not w.claims
    assert w.execute("compare_claim_ratio", args)["reused_local_receipt"]
    assert not w.snapshot()["passed"]


@pytest.mark.parametrize("change,issue", [
    ({"value": "26000"}, "calculation_uncited_operand"),
    ({"quote": "52,445条任务中26,559条"}, "claim_unbound_evidence"),
    ({"source": "nonexistent"}, "claim_unbound_evidence"),
])
def test_ratio_cannot_invent_operand_source_or_quote(change, issue):
    w = workspace()
    args = arguments(w)
    args["numerator"].update(change)
    assert w.execute("compare_claim_ratio", args)["issue"] == issue
    assert not w.snapshot()["failed"]


def test_wrong_field_is_located_but_never_automatically_accepted():
    w = ReasoningWorkspace([{"id": "scope", "requirement": "Check answer", "allow_na": False}], {},
                           {"reasoning": "A bounded explanation.", "answer": "Actual answer."}, phase="final")
    entry = check_entry(w)
    entry["observations"][0]["evidence"] = [{"source": "reasoning", "quote": "Actual answer."}]
    result = w.execute("record_check", entry)
    assert not result["ok"] and not w.checks
    detail = result["recovery"]["details"][0]
    assert detail["diagnosis"] == "wrong_source_field"
    assert detail["exact_matches_elsewhere"] == [{"source": "answer", "start": 0, "end": 14}]
    assert entry["observations"][0]["evidence"][0]["source"] == "reasoning"


def test_historical_closing_quote_boundary_corrected_without_weakening_context():
    fixture = json.loads((Path(__file__).parent / "fixtures/reasoning_agent/environment_sessions.json").read_text(encoding="utf-8"))
    inputs = fixture["sessions"][2]["inputs"]
    body = inputs["candidate"]
    old = ReasoningEvidence(inputs["rules"], {}, body)
    w = ReasoningWorkspace(inputs["rules"], {}, body, phase="final")
    a = next(a for a in w.anchors if a["id"] == "c_b6a827316022c229")
    assert next(prior for prior in old.anchors if prior["id"] == a["id"])["quote"].startswith("” ")
    assert a["quote"].startswith("For example,")
    entry = claim_entry(w, a)
    assert w.execute("record_claim", entry)["ok"]
    broken = ReasoningWorkspace(inputs["rules"], {}, body, phase="final")
    entry["interpretation"]["context_quote"] = "Write an essay about school safety"
    assert broken.execute("record_claim", entry)["issue"] == "claim_context_unbound"
    assert w.candidate == body


def test_redundant_commit_error_instructs_skip_not_repair_loop():
    w = ReasoningWorkspace([{"id": "scope", "requirement": "Check answer", "allow_na": False}], {},
                           {"reasoning": "A bounded explanation. Another bounded claim."}, phase="prefix")
    entry = check_entry(w)
    assert w.execute("record_check", entry)["ok"]
    prior = deepcopy(w.checks)
    entry["evidence"][0]["quote"] = "A bounded explanation."
    result = w.execute("record_check", entry)
    assert not result["ok"] and result["category"] == "committed_record_conflict"
    assert "Do not retry" in result["recovery"]["action"] and w.checks == prior


def test_calculation_input_error_is_not_an_accepted_receipt_or_a_candidate_defect():
    w = workspace("52,445 条任务中 26,559 条匹配，占50.6%。")
    args = {"anchor_id": w.anchors[0]["id"], "evidence": [{"source": "reasoning", "quote": w.candidate["reasoning"]}],
            "calculation": {"operation": "percent_of", "baseline": "52445", "value": "26559", "reported": "50.6",
                            "reported_quote": "占50.6%", "metric_kind": "proportion", "operand_scale": "native", "reported_unit": "percent"}}
    receipt = w.execute("calculate_claim", args)
    assert not receipt["ok"] and receipt["category"] == "tool_input_error"
    assert receipt["issue"] == "calculation_scale_mismatch" and not w.snapshot()["failed"]
    assert w.execute("calculate_claim", args)["reused_local_receipt"]
    args["calculation"]["metric_kind"] = "quantity"
    receipt = w.execute("calculate_claim", args)
    assert receipt["ok"] and receipt["receipt"]["passed"] and receipt["category"] == "conditional_calculation"
    assert not w.claims and not w.snapshot()["passed"]


def test_registered_citation_ids_expand_exactly_without_losing_negation():
    w = workspace("One claim cannot prove all cases.")
    a = w.anchors[0]
    entry = claim_entry(w, a)
    entry["evidence"] = [{"citation_id": a["id"]}]
    entry["interpretation"].pop("context_quote")
    entry["interpretation"]["context_citation_id"] = a["id"]
    before = deepcopy(entry)
    result = w.execute("record_claim", entry)
    assert result["ok"] and result["input_repairs"]
    assert w.claims[a["id"]]["evidence"] == [{"source": "reasoning", "quote": a["quote"]}]
    assert "cannot" in w.interpretations[a["id"]]["context_quote"]
    assert entry == before and w.candidate["reasoning"] == a["quote"]


def test_citation_id_never_waives_scope_test_or_literal_conflict():
    w = workspace("One claim cannot prove all cases.")
    entry = claim_entry(w, w.anchors[0])
    entry["evidence"] = [{"citation_id": w.anchors[0]["id"]}]
    entry["counterexample"] = ""
    assert w.execute("record_claim", entry)["issue"] == "claim_missing_scope_test"
    entry["evidence"][0]["quote"] = "A different claim"
    assert w.execute("record_claim", entry)["issue"] == "invalid_tool_arguments"
    assert not w.claims


def test_unknown_citation_id_is_local_to_batch_item_and_rejects_foreign_context():
    w = workspace("There are 2 counts. There are 3 counts.")
    first, second = (claim_entry(w, a) for a in w.anchors)
    first["evidence"] = [{"citation_id": w.anchors[0]["id"]}]
    second["evidence"] = [{"citation_id": "made-up"}]
    result = w.execute("submit_review", {"checks": [], "claims": [first, second]})
    assert result["entries"][0]["result"]["ok"]
    assert result["entries"][1]["result"]["issue"] == "unknown_or_conflicting_citation_id"
    assert len(w.claims) == 1
    second["evidence"] = [{"citation_id": w.anchors[1]["id"]}]
    second["interpretation"].pop("context_quote")
    second["interpretation"]["context_citation_id"] = w.anchors[0]["id"]
    assert w.execute("record_claim", second)["issue"] == "claim_context_unbound"
    assert len(w.claims) == 1


def test_verbal_approximation_can_be_computed_without_fabricating_reported_number():
    text = "52,445 条任务中有26,559条匹配，约一半。"
    w = workspace(text)
    result = w.execute("compare_claim_ratio", arguments(w, citation=text))
    assert result["ok"] and result["percent"].startswith("50.6416")
    assert result["comparison_holds"] is None and result["candidate_verdict"] is None
    assert not w.claims and w.candidate["reasoning"] == text


@pytest.mark.parametrize("reported", ["50.6%", "约一半", "26,559"])
def test_decimal_contract_explains_invalid_format_before_calculation(reported):
    w = workspace("占50.6%。")
    args = {"anchor_id": w.anchors[0]["id"], "evidence": [],
        "calculation": {"operation": "percent_of", "baseline": "1000", "value": "506", "reported": reported,
                        "reported_quote": "50.6%", "metric_kind": "quantity", "operand_scale": "native", "reported_unit": "percent"}}
    result = w.execute("calculate_claim", args)
    assert result["issue"] == "invalid_tool_arguments"
    assert result["recovery"]["details"][0]["path"] == "$.calculation.reported"
    assert not w.snapshot()["failed"]
