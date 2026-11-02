"""Temporary operator instructions bind one fixed answer without changing its task."""
from copy import deepcopy

import pytest

from lib.contexts.workflows.domain.dialogue import dialogue_digest as digest
from lib.contexts.workflows.domain.planned_sft_reanswer import (
    planned_sft_task_instruction, reanswer_options, reanswer_receipt,
    valid_reanswer_receipt, validate_reanswer_task_instructions,
)


def test_temporary_instruction_validation_preserves_literal_text_and_selected_subset():
    note = '  核对本条的 NA 含义。\n保留 JSON 示例 {"NA": null}，不把未知项当事实。  '
    supplied = {"second": note}
    validated = validate_reanswer_task_instructions(supplied, ["first", "second"])
    assert validated == supplied and validated is not supplied
    assert validated["second"] == note
    assert validate_reanswer_task_instructions(None, ["first"]) == {}
    assert validate_reanswer_task_instructions({}, ["first"]) == {}


@pytest.mark.parametrize("notes,code", [
    ({"unselected": "检查范围。"}, "not_selected"),
    ({1: "检查范围。"}, "not_selected"),
    ([], "invalid_sft_reanswer_task_instructions"),
    ({"first": " "}, "invalid_sft_reanswer_task_instructions"),
    ({"first": 1}, "invalid_sft_reanswer_task_instructions"),
    ({"first": "禁止\x00字符"}, "invalid_sft_reanswer_task_instructions"),
    ({"first": "\ud800"}, "invalid_sft_reanswer_task_instructions"),
])
def test_invalid_or_unselected_operator_instructions_fail_at_input_boundary(notes, code):
    with pytest.raises(ValueError, match=code):
        validate_reanswer_task_instructions(notes, ["first"])


def test_long_task_instruction_is_preserved_without_an_artificial_note_length_limit():
    note = "核对这一条的具体依据与适用范围。\n" * 500
    assert len(note) > 6000
    validated = validate_reanswer_task_instructions({"first": note}, ["first"])
    assert validated["first"] == note
    rendered = planned_sft_task_instruction({"sft_reanswer_task_instructions_version": 1,
        "sft_reanswer_task_instructions": validated}, "first")
    assert note in rendered


def test_only_explicit_nonempty_notes_get_a_frozen_version_and_generation_scope():
    parent = {"name": "Original", "qa_director": {"answer_rules": "原规则"}}
    selected = [{"record": {"id": "first", "review_observation": {"verdict": {
        "keep": True, "reason": "通过，回答正确。"}}}}]
    old = reanswer_options(parent, selected, None)
    assert old == reanswer_options(parent, selected, None, task_instructions={})
    assert "sft_reanswer_task_instructions_version" not in old
    assert planned_sft_task_instruction({}, "first") == ""
    notes = {"first": "按资料核对本条约束，不重复已经解决的疑问。"}
    options = reanswer_options(parent, selected, None, task_instructions=notes)
    assert options["sft_reanswer_task_instructions"] == notes
    assert options["sft_reanswer_task_instructions"] is not notes
    assert options["agent_experience"] is None and parent["qa_director"]["answer_rules"] == "原规则"
    rendered = planned_sft_task_instruction(options, "first")
    assert notes["first"] in rendered and "操作者" in rendered and "不是实际用户的新发言" in rendered
    assert "本条重答必须遵循" in rendered
    assert "通过，回答正确" not in rendered
    assert planned_sft_task_instruction(options, "second") == ""


def test_note_receipt_keeps_task_identity_and_separately_binds_instruction_provenance():
    question = {"role": "user", "content": "按资料解释维护的前置条件。"}
    contract = {"question": question["content"], "qa_type": "grounded", "guidance": "核对实际资料。"}
    source = {"scope": "selected_lines", "text_sha256": digest("维护前断电。")}
    original = {"id": "first", "messages": [question, {"role": "assistant", "content": "旧回答。"}],
        "qa_contract": contract, "provided_source_context": source}
    generated = {**deepcopy(original), "messages": [deepcopy(question), {
        "role": "assistant", "content": "维护前先断电。", "reasoning_content": "先核对资料里的前置动作。"}]}
    context = {"parent_run_id": "parent", "candidate_id": "first", "candidate_sha256": digest(original),
        "evidence": {"teacher_evidence": "维护前断电。"}, "evidence_sha256": digest("维护前断电。")}
    legacy = reanswer_receipt(original, context, [question], generated, {}, {})
    assert "task_instruction" not in legacy
    note = "只处理这条回答里尚未解决的资料范围问题。"
    recipe = {"sft_reanswer_task_instructions_version": 1, "sft_reanswer_task_instructions": {"first": note}}
    receipt = reanswer_receipt(original, context, [question], generated, {}, recipe)
    assert receipt["original_task_sha256"] == legacy["original_task_sha256"] == receipt["current_task_sha256"]
    assert receipt["question_sha256"] == legacy["question_sha256"]
    assert receipt["contract_sha256"] == legacy["contract_sha256"]
    assert receipt["task_instruction"]["text"] == note
    assert receipt["task_instruction"]["origin"] == "operator_task_instruction"
    assert receipt["task_instruction"]["request_role"] == "system"
    assert receipt["task_instruction"]["system_instruction_sha256"] == digest(planned_sft_task_instruction(recipe, "first"))
    generated["sft_reanswer"] = receipt
    assert valid_reanswer_receipt(generated)
    changed = deepcopy(generated)
    changed["sft_reanswer"]["task_instruction"]["request_role"] = "user"
    changed["sft_reanswer"]["sha256"] = digest({key: value for key, value in changed["sft_reanswer"].items() if key != "sha256"})
    assert not valid_reanswer_receipt(changed)
