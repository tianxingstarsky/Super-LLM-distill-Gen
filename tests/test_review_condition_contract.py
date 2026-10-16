"""Premise references expose invented conditions without judging prose truth."""
from copy import deepcopy

import pytest

from lib.domain.claim_review import claim_review_requests
from lib.domain.review_target_units import target_units
from lib.domain.review_condition_contract import (
    SEMANTIC_VERIFICATION, condition_ledger, validate_premises,
)


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    # This module is entirely offline; override the repository's HTTP fixture.
    yield


def request():
    payload = {"messages": [
        {"role": "user", "content": "We already downloaded the files. Can I read them offline?"},
        {"role": "assistant", "content": "The second option is local reading.",
         "reasoning_content": "It costs nothing."},
        {"role": "user", "content": "Use the second option. There is no concurrent writer."},
        {"role": "assistant", "content": "If files were downloaded, local reading is possible.\n\nTry a snapshot.",
         "reasoning_content": "Availability is distinct from a later write."},
    ]}
    source = {"teacher_evidence_blocks": ["Saved copies remain readable before the replacement is written."]}
    req = claim_review_requests("multiturn", payload, source, True)[-1]
    req["target_units"] = target_units(req["target_fields"], req["scope"]["message_index"])
    return req


def premise(origin, use, quote, **locator):
    return {"origin": origin, "use": use, "quote": quote, **locator}


def blank_ledger(req):
    return [{"unit_id": unit["id"], "premises": []} for unit in req["target_units"]]


def test_exact_declared_conditions_and_actual_user_constraints_are_accepted():
    req = request()
    items = [
        premise("candidate", "task_condition", "If files were downloaded", unit_id=req["target_units"][0]["id"]),
        premise("context", "task_condition", "We already downloaded the files", message_index=0, field="content"),
        premise("context", "task_condition", "There is no concurrent writer.", message_index=2, field="content"),
        premise("source", "support", "Saved copies remain readable", source_path=["teacher_evidence_blocks", 0]),
    ]
    frozen = deepcopy((items, req))
    result = validate_premises(items, req)
    assert (items, req) == frozen
    assert result["premises"] == items
    assert result["required_assumptions"] == result["counterexample_assumptions"] == []
    assert result["semantic_verification"] == SEMANTIC_VERIFICATION
    result["premises"][0]["quote"] = "changed output"
    assert (items, req) == frozen


def test_historical_assistant_reference_is_not_promoted_to_factual_support():
    req = request()
    item = premise("context", "reference_resolution", "The second option is local reading.", message_index=1, field="content")
    assert validate_premises([item], req)["premises"] == [item]
    for use in ("task_condition", "support", "counterexample"):
        with pytest.raises(ValueError, match="condition_historical_assistant_not_evidence"):
            validate_premises([{**item, "use": use}], req)
    with pytest.raises(ValueError, match="condition_historical_assistant_not_evidence"):
        validate_premises([premise("context", "support", "It costs nothing.",
                                   message_index=1, field="reasoning_content")], req)


def test_candidate_can_supply_condition_or_referent_but_cannot_self_support():
    req = request()
    item = premise("candidate", "reference_resolution", "Try a snapshot.", unit_id=req["target_units"][1]["id"])
    assert validate_premises([item], req)["premises"] == [item]
    with pytest.raises(ValueError, match="condition_candidate_cannot_self_support"):
        validate_premises([{**item, "use": "support"}], req)


def test_actual_tool_context_can_supply_quoted_support_without_certifying_truth():
    req = request()
    req["messages"][1] = {"role": "tool", "content": "The saved version is available."}
    item = premise("context", "support", "The saved version is available.", message_index=1, field="content")
    checked = validate_premises([item], req)
    assert checked["premises"] == [item]
    assert checked["semantic_verification"] == "provenance_only_not_entailment"


@pytest.mark.parametrize("use", ["task_condition", "reference_resolution", "support"])
def test_invented_restricting_condition_is_preserved_and_flagged_not_certified(use):
    req = request()
    item = premise("assumption", use, "Only newly created files are visible to the reader.")
    result = validate_premises([item], req)
    assert result["premises"] == [item]
    assert result["required_assumptions"] == [{"premise_index": 0, "premise": item}]
    assert result["counterexample_assumptions"] == []
    assert "keep" not in result and "status" not in result


def test_counterexample_assumptions_are_separate_and_cannot_become_silent_support():
    req = request()
    item = premise("assumption", "counterexample", "A saved copy exists before its next replacement.")
    result = validate_premises([item], req)
    assert result["required_assumptions"] == []
    assert result["counterexample_assumptions"] == [{"premise_index": 0, "premise": item}]
    # Supported callers must inspect both lists, rather than interpreting an
    # empty required list as unconditional proof. Declared uses stay untouched.
    assert result["premises"][0]["use"] == "counterexample"


@pytest.mark.parametrize("item,error", [
    (premise("context", "support", "files never existed", message_index=0, field="content"), "fabricated_claim_review_evidence_quote"),
    (premise("context", "support", "Try a snapshot.", message_index=3, field="content"), "invalid_claim_review_context_reference"),
    (premise("context", "support", "future", message_index=4, field="content"), "invalid_claim_review_context_reference"),
    (premise("context", "support", "We already", message_index=True, field="content"), "invalid_claim_review_context_reference"),
    (premise("context", "support", "We already", message_index=0, field="invented_field"), "invalid_claim_review_context_reference"),
    (premise("source", "support", "Copies cannot be read", source_path=["teacher_evidence_blocks", 0]), "fabricated_claim_review_evidence_quote"),
    (premise("source", "support", "Saved copies", source_path=["teacher_evidence_blocks", 1]), "invalid_claim_review_source_path"),
    (premise("source", "support", "Saved copies", source_path=["teacher_evidence_blocks", True]), "invalid_claim_review_source_path"),
    (premise("source", "support", "Saved copies", source_path="teacher_evidence_blocks.0"), "invalid_claim_review_source_path"),
    (premise("candidate", "task_condition", "other text", unit_id="m3u0001"), "fabricated_condition_candidate_quote"),
    (premise("candidate", "task_condition", "local reading", unit_id="m1u0001"), "invalid_condition_candidate_unit"),
    (premise("candidate", "task_condition", "Try a snapshot.", unit_id="m3u0001"), "fabricated_condition_candidate_quote"),
])
def test_forged_or_out_of_scope_references_are_rejected(item, error):
    with pytest.raises(ValueError, match=error):
        validate_premises([item], request())


@pytest.mark.parametrize("items,error", [
    (None, "invalid_condition_premises"),
    ({}, "invalid_condition_premises"),
    ([None], "invalid_condition_premise"),
    ([{}], "invalid_condition_premise"),
    ([premise("unknown", "support", "text")], "invalid_condition_premise"),
    ([premise("assumption", "unknown", "text")], "invalid_condition_premise"),
    ([premise("assumption", "support", "")], "missing_condition_premise_quote"),
    ([premise("assumption", "support", "  ")], "missing_condition_premise_quote"),
    ([premise("assumption", "support", "a\x00b")], "missing_condition_premise_quote"),
    ([premise("assumption", "support", "text", source_path=[])], "invalid_condition_premise"),
    ([premise("assumption", "support", "text", confidence=1)], "invalid_condition_premise"),
    ([premise("context", "support", "text", message_index=0)], "invalid_condition_premise"),
    ([{"origin": [], "use": "support", "quote": "text"}], "invalid_condition_premise"),
    ([{"origin": "assumption", "use": [], "quote": "text"}], "invalid_condition_premise"),
])
def test_strict_premise_fields_and_enums(items, error):
    with pytest.raises(ValueError, match=error):
        validate_premises(items, request())


def test_empty_premises_and_ordinary_advice_need_not_invent_a_condition():
    req = request()
    raw = blank_ledger(req)
    result = condition_ledger(raw, req)
    assert result["required_assumptions"] == result["counterexample_assumptions"] == []
    assert [unit["unit_id"] for unit in result["units"]] == [unit["id"] for unit in req["target_units"]]
    assert all(unit["premises"] == [] for unit in result["units"])
    assert result["semantic_verification"] == SEMANTIC_VERIFICATION


def test_ledger_aggregates_separate_assumptions_with_unit_and_premise_binding():
    req = request()
    raw = blank_ledger(req)
    raw[0]["premises"] = [
        premise("assumption", "support", "The reader sees only the latest batch."),
        premise("assumption", "counterexample", "Another reader retains a snapshot."),
    ]
    frozen = deepcopy((raw, req))
    result = condition_ledger(raw, req)
    assert (raw, req) == frozen
    assert result["required_assumptions"] == [{"unit_id": raw[0]["unit_id"], "premise_index": 0,
                                                "premise": raw[0]["premises"][0]}]
    assert result["counterexample_assumptions"] == [{"unit_id": raw[0]["unit_id"], "premise_index": 1,
                                                    "premise": raw[0]["premises"][1]}]


@pytest.mark.parametrize("change,error", [
    (lambda rows: rows.pop(), "invalid_condition_ledger_coverage"),
    (lambda rows: rows.append(deepcopy(rows[0])), "invalid_condition_ledger_coverage"),
    (lambda rows: rows[0].update(unit_id="m1u0001"), "invalid_condition_ledger_coverage"),
    (lambda rows: rows[0].update(unit_id=[]), "invalid_condition_ledger_coverage"),
    (lambda rows: rows[0].update(status="supported"), "invalid_condition_ledger_entry"),
    (lambda rows: rows[0].pop("premises"), "invalid_condition_ledger_entry"),
    (lambda rows: rows.__setitem__(0, None), "invalid_condition_ledger_entry"),
])
def test_ledger_requires_exactly_every_current_unit(change, error):
    req = request()
    raw = blank_ledger(req)
    change(raw)
    with pytest.raises(ValueError, match=error):
        condition_ledger(raw, req)


def test_ledger_can_be_reordered_without_altering_any_id_or_original_input():
    req = request()
    raw = list(reversed(blank_ledger(req)))
    assert [unit["unit_id"] for unit in condition_ledger(raw, req)["units"]] == [row["unit_id"] for row in raw]


@pytest.mark.parametrize("value", [None, {}, "ledger"])
def test_ledger_is_an_array(value):
    with pytest.raises(ValueError, match="invalid_condition_ledger"):
        condition_ledger(value, request())


@pytest.mark.parametrize("change,error", [
    (lambda req: req.update(scope=None), "invalid_condition_contract_request"),
    (lambda req: req["scope"].update(message_index=True), "invalid_condition_contract_prefix"),
    (lambda req: req["messages"].append({"role": "user", "content": "future"}), "invalid_condition_contract_prefix"),
    (lambda req: req["messages"][-1].update(role="user"), "invalid_condition_contract_prefix"),
    (lambda req: req["target_units"][0].update(text="forged unit"), "invalid_condition_contract_units"),
])
def test_request_boundary_checks_fail_before_any_premise_is_accepted(change, error):
    req = request()
    change(req)
    with pytest.raises(ValueError, match=error):
        validate_premises([], req)


def test_opt_in_uses_finer_segmentation_and_old_request_keeps_old_units():
    req = request()
    req["target_fields"]["content"] = "First sentence. Second sentence. Third sentence."
    req["target_units"] = target_units(req["target_fields"], req["scope"]["message_index"])
    assert len(condition_ledger(blank_ledger(req), req)["units"]) == 2
    req["condition_contract_version"] = 1
    req["target_units"] = target_units(req["target_fields"], req["scope"]["message_index"], segmentation_version=2)
    assert len(condition_ledger(blank_ledger(req), req)["units"]) == 4
    req.pop("condition_contract_version")
    with pytest.raises(ValueError, match="invalid_condition_contract_units"):
        validate_premises([], req)


@pytest.mark.parametrize("version", [True, False, 0, 2, "1", []])
def test_unknown_opt_in_contract_versions_do_not_reinterpret_unit_boundaries(version):
    req = request()
    req["condition_contract_version"] = version
    with pytest.raises(ValueError, match="invalid_condition_contract_version"):
        validate_premises([], req)


def test_no_truth_rules_trigger_on_conditional_words_or_topic_names():
    req = request()
    raw = [premise("assumption", "counterexample", "若必须重试，则只读；MoonHarbor is not a truth label.")]
    checked = validate_premises(raw, req)
    assert checked["premises"] == raw
    assert checked["required_assumptions"] == []
    assert len(checked["counterexample_assumptions"]) == 1
