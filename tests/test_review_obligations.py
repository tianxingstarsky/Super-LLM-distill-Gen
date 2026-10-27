"""Correct controls, minimal mutations and frozen-record coverage; no model calls."""
from copy import deepcopy
import pytest

from lib.contexts.workflows.domain.review_obligations import (
    reviewed_dialogue, review_obligations, quantity_issue, decimal_change)
from lib.contexts.workflows.domain.obligation_review import ObligationReview
from lib.contexts.workflows.domain.review_source_inventory import evidence_rules
from lib.contexts.workflows.domain.presentation_admission import style_admission
from lib.contexts.workflows.domain.reasoning_checklist import validate_reasoning_checklist

RULES = [{"id": "task", "requirement": "Answer correctly with all requested constraints.", "allow_na": False}]
SOURCE = "Percentage of responses rated A: baseline 44.4%, improved 54.4%."


@pytest.mark.parametrize("operation,reported,text,issue", [
    ("percentage_points", "10", "A-rated responses improved by 10 percentage points.", None),
    ("relative_percent", "22.52", "The relative increase in A-rated responses is 22.52%.", None),
    ("relative_percent", "10", "Responses improved by 10%.", "quantity_calculation_mismatch"),
    ("percentage_points", "10", "Responses improved by 10%.", "percentage_change_unit_ambiguous"),
    ("percentage_points", "11", "Responses improved by 11 percentage points.", "quantity_calculation_mismatch"),
])
def test_percent_point_mutations_preserve_mathematically_correct_controls(operation, reported, text, issue):
    assert quantity_issue({"operation": operation, "baseline": "44.4", "current": "54.4", "reported": reported}, SOURCE, text) == issue


def test_score_point_units_cannot_be_called_percentage_points():
    calc = {"operation": "score_points", "baseline": "6.8", "current": "39.9", "reported": "33.1"}
    assert quantity_issue(calc, "ROUGE-L: 6.8 to 39.9", "ROUGE-L increased by 33.1 points") is None
    assert quantity_issue(calc, "ROUGE-L: 6.8 to 39.9", "ROUGE-L increased by 33.1 percentage points") == "score_points_are_not_percentage_points"
    assert quantity_issue({**calc, "baseline": "1.8"}, "ROUGE-L: 6.8 to 39.9", "33.1 points") == "quantity_operand_not_in_external_evidence"


def test_numbers_alone_do_not_certify_percentage_axis():
    calc = {"operation": "percentage_points", "baseline": "44.4", "current": "54.4", "reported": "10"}
    assert quantity_issue(calc, "44.4 54.4", "10 percentage points") == "percentage_metric_evidence_missing"
    with pytest.raises(ValueError, match="zero_baseline"):
        decimal_change("relative_percent", "0", "10")


def workspace(body=None, context=None):
    return ObligationReview(evidence_rules(RULES), context or {
        "prompt": [{"role": "user", "content": "What changed?"}], "source": {"text": SOURCE}},
        body or {"reasoning": "A-rated responses improved by 10 percentage points.", "answer": "A clear improvement."}, phase="final")


def review(w):
    return {"covered_sections": [s["id"] for s in w.sections], "findings": [], "risk_checks": [],
        "checks": [{"rule_id": r["id"], "status": "met", "reason": "Actual supported assessment.",
                    "evidence": [{"source": "reasoning", "quote": w.candidate["reasoning"]}]} for r in w.rules]}


def test_supported_quantity_needs_grounded_operands_and_metric():
    w = workspace(); r = review(w)
    assert w.validate_review(r) == "risk_coverage_missing"
    r["risk_checks"] = [{"id": w.obligations[0]["id"], "status": "supported", "reason": "Subtract the cited A-rating percentages.",
        "evidence": [{"source": "reasoning", "quote": w.candidate["reasoning"]}, {"source": "source.text", "quote": SOURCE}],
        "quantities": [{"operation": "percentage_points", "baseline": "44.4", "current": "54.4", "reported": "10"}]}]
    assert w.validate_review(r) is None
    before = deepcopy(r)
    assert w.execute("submit_semantic_review", r)["ok"]
    assert r == before and w.snapshot()["assessment"]["risk_checks"] == r["risk_checks"]
    corrupted = deepcopy(r); corrupted["risk_checks"][0]["evidence"].pop()
    assert w.validate_review(corrupted) == "quantity_metric_and_operands_required"


def test_negative_risk_cannot_be_replaced_by_generic_pass():
    w = workspace(); r = review(w)
    r["risk_checks"] = [{"id": w.obligations[0]["id"], "status": "defect", "reason": "Wrong comparison unit.",
        "evidence": [{"source": "reasoning", "quote": w.candidate["reasoning"]}], "quantities": []}]
    assert w.validate_review(r) == "negative_risk_requires_finding"
    r["risk_checks"][0]["status"] = "not_applicable"
    assert w.validate_review(r) == "required_risk_waived"


def test_history_is_reviewed_even_when_final_answer_is_correct():
    context = {"prompt": [{"role": "user", "content": "Compare scores."},
        {"role": "assistant", "content": "ROUGE-L improved by 33.1 percentage points.", "reasoning_content": "An old unsupported thought."},
        {"role": "user", "content": "Is that right?"}], "source": {"text": "6.8 39.9 ROUGE-L"}}
    body = {"reasoning": "ROUGE-L is a score.", "answer": "Those are score points."}
    w = workspace(body, context)
    assert w.candidate["history_1_content"] == context["prompt"][1]["content"]
    assert "history_1_reasoning_content" in w.candidate
    r = review(w); r["covered_sections"] = [s for s in r["covered_sections"] if not s.startswith("history_")]
    assert w.validate_review(r) == "full_body_coverage_missing"
    assert body == {"reasoning": "ROUGE-L is a score.", "answer": "Those are score points."}


def test_scope_detects_chinese_visible_material_without_only_restriction():
    ctx = {"prompt": [{"role": "user", "content": "资料：seed tasks\n用户消息：这两段连起来怎么实现？"}]}
    assert any(o["kind"] == "scope" for o in review_obligations(ctx, {"answer": "按文中流程……"}))


def test_bilingual_requirement_is_conditional_and_cannot_be_advisory():
    ctx = {"generation_style": {"reasoning_language": "zh_en"}}
    assert any(o["kind"] == "bilingual" for o in review_obligations(ctx, {"reasoning": "Only English."}))
    assert not review_obligations({}, {"reasoning": "Only English."})
    cfg = {"version": 6, "rules": RULES, "evidence_policy": 2, "style_admission": "advisory"}
    assert validate_reasoning_checklist(cfg) == cfg
    assert not style_admission(cfg, {"keep": True, "adherence": 3})["allowed"]
    assert style_admission({**cfg, "evidence_policy": 1}, {"keep": True, "adherence": 3})["allowed"]


def test_unrelated_change_and_proportion_do_not_create_quantity_risk():
    body = {"reasoning": "可以增加覆盖检查。复核发现 92% 的任务有效。"}
    assert not review_obligations({}, body)


def test_legacy_tools_and_schema_are_unchanged():
    from lib.contexts.workflows.domain.semantic_review import semantic_tools
    before = deepcopy(semantic_tools())
    workspace().tools()
    assert semantic_tools() == before


def test_new_risks_preserve_precise_missing_candidate_feedback():
    w = workspace(); r = review(w)
    r["checks"][0]["evidence"] = [{"source": "source.text", "quote": SOURCE}]
    response = w.execute("submit_semantic_review", r)
    assert response["issue"] == "rule_citation_not_in_body"
    assert any(d["path"] == "$.checks[0].evidence" and d.get("message") == "missing_candidate_evidence"
               for d in response["recovery"]["details"])
    assert r["checks"][0]["status"] == "met" and not w.snapshot()["passed"]


def test_empty_risk_schema_is_valid_and_legacy_review_still_needs_risk_field():
    from jsonschema import Draft202012Validator
    w = workspace({"reasoning": "A conditional fact.", "answer": "Apply its condition."})
    assert not w.obligations
    Draft202012Validator.check_schema(w.tools()[0]["function"]["parameters"])
    r = review(w)
    assert w.validate_review(r) is None
    r.pop("risk_checks")
    assert w.validate_review(r) == "risk_review_fields"


def test_quantity_contract_locates_prose_in_numeric_fields_without_changing_vote():
    w = workspace(); r = review(w)
    r["risk_checks"] = [{"id": w.obligations[0]["id"], "status": "supported", "reason": "Claim under review.",
        "evidence": [{"source": "reasoning", "quote": w.candidate["reasoning"]}, {"source": "source.text", "quote": SOURCE}],
        "quantities": [{"operation": "percentage_points", "baseline": "44.4 (A rating)", "current": "54.4", "reported": "10"}]}]
    before = deepcopy(r)
    response = w.execute("submit_semantic_review", r)
    assert not response["ok"] and r == before
    assert any(d["path"] == "$.risk_checks[0].quantities[0].baseline" for d in response["recovery"]["details"])
    r["risk_checks"][0]["quantities"][0]["baseline"] = "43.4"
    response = w.execute("submit_semantic_review", r)
    assert any(d["path"] == "$.risk_checks[0]" and d["message"] == "quantity_operand_not_in_external_evidence"
               for d in response["recovery"]["details"])


def test_relative_change_of_score_does_not_require_percentage_metric():
    calc = {"operation": "relative_percent", "baseline": "6.8", "current": "39.9", "reported": "486.8"}
    assert quantity_issue(calc, "ROUGE-L scores 6.8 and 39.9", "Relative improvement is 486.8%.") is None
    calc.update(operation="percentage_points", reported="33.1")
    assert quantity_issue(calc, "ROUGE-L scores 6.8 and 39.9; prose says 33.1%", "ROUGE-L improved by 33.1 percentage points.") == "score_points_are_not_percentage_points"


def test_calculator_recovery_identifies_only_the_missing_operand():
    w = workspace()
    response = w.execute("calculate_numbers", {"operation": "difference", "left": "54.4", "right": "49.5",
        "evidence": [{"source": "source.text", "quote": SOURCE}]})
    assert response["issue"] == "uncited_operand"
    assert [d["path"] for d in response["recovery"]["details"]] == ["$.right"]
    assert w.snapshot()["passed"] is False
