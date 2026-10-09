"""Typed QA scheduling preserves user control and evidence visibility."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from lib.application.workflow_service import WorkflowApplication
from lib.domain.workflow_graph import execution_graph
from lib.domain.workflow_node_prompts import (
    LEGACY_NODE_PROMPT_IDS, VERSION_10_NODE_PROMPT_IDS, NODE_PROMPT_IDS,
    active_node_prompt_ids, builtin_node_prompt, snapshot_node_prompts,
    validate_node_prompt_snapshot,
)
from lib.domain.workflow_qa_director import (
    DEFAULT_TYPE_WEIGHTS, DEFAULT_ANSWER_RULES, QA_TYPES, allocate_qa_types,
    validate_director_task, validate_qa_director,
)
from lib.domain.workflow_scale import generation_units, generation_variant, node_roles


def task(qa_type="grounded", **changes):
    value = {"id": "candidate-1", "qa_type": qa_type, "question": "How do I stop the device?",
             "visible_context": "Disconnect power. Record the result.", "answer_policy": "answer",
             "guidance": "Keep the answer concise.", "evidence_quotes": ["Disconnect power."]}
    return {**value, **changes}


def test_recipe_is_opt_in_and_copies_mutable_user_rules_and_weights():
    assert validate_qa_director(None) == validate_qa_director({}) == {"enabled": False}
    supplied = {"enabled": True, "question_rules": "  ASK SPECIFIC QUESTIONS  ",
                "answer_rules": "Give a concise answer.", "type_weights": dict(DEFAULT_TYPE_WEIGHTS)}
    original = deepcopy(supplied)
    normalized = validate_qa_director(supplied)
    assert normalized["question_rules"] == "ASK SPECIFIC QUESTIONS"
    assert normalized["batch_size"] == 20 and normalized["history_limit"] == 10
    normalized["type_weights"]["grounded"] = 0
    assert supplied == original


@pytest.mark.parametrize("value", [
    False, [], {"enabled": 1}, {"enabled": True, "unknown": "ignored"},
    {"enabled": True, "batch_size": 51}, {"enabled": True, "batch_size": True},
    {"enabled": True, "history_limit": -1}, {"enabled": True, "history_limit": 21},
    {"enabled": True, "type_weights": {"grounded": 100}},
    {"enabled": True, "type_weights": dict.fromkeys(QA_TYPES, 0)},
    {"enabled": True, "type_weights": {**DEFAULT_TYPE_WEIGHTS, "grounded": float("nan")}},
    {"enabled": True, "type_weights": {**DEFAULT_TYPE_WEIGHTS, "grounded": True}},
    {"enabled": True, "question_rules": " "}, {"enabled": True, "answer_rules": "x" * 12001},
    {"enabled": True, "answer_rules": "a\x00b"},
])
def test_invalid_config_never_reaches_driver(value):
    driver = Mock()
    with pytest.raises(ValueError, match="invalid_qa_director"):
        WorkflowApplication(driver).create_run(targets=["sft"], qa_director=value)
    driver.create.assert_not_called()


def test_application_forwards_rules_and_requires_a_generation_target():
    driver = Mock()
    rules = {"enabled": True, "question_rules": "USER QUESTION RULE", "answer_rules": "USER ANSWER RULE"}
    WorkflowApplication(driver).create_run(targets=["dpo"], qa_director=rules)
    forwarded = driver.create.call_args.kwargs["qa_director"]
    assert forwarded["question_rules"] == rules["question_rules"]
    assert forwarded["answer_rules"] == rules["answer_rules"]
    driver.reset_mock()
    with pytest.raises(ValueError, match="qa_director_requires_qa_target"):
        WorkflowApplication(driver).create_run(targets=["cpt", "agent"], qa_director=rules)
    driver.create.assert_not_called()


def test_director_controls_only_generated_qa_paths_including_implicit_sft():
    targets = ["cpt", "dpo", "multiturn", "agent"]
    nodes, edges = execution_graph(targets, qa_director={"enabled": True})
    assert nodes[:2] == ("ingest", "director")
    assert ("ingest", "director") in edges
    assert ("director", "sft") in edges and ("director", "multiturn") in edges
    assert ("ingest", "sft") not in edges and ("ingest", "multiturn") not in edges
    assert ("ingest", "cpt") in edges and ("ingest", "agent") in edges
    assert "director" not in execution_graph(["cpt"], qa_director=True)[0]
    assert node_roles("director", "文档资料") == ("generation",)


def test_incomplete_editable_draft_keeps_graph_and_prompts_available():
    incomplete = {"enabled": True, "type_weights": dict.fromkeys(QA_TYPES, 0), "question_rules": ""}
    assert "director" in execution_graph(["sft"], qa_director=incomplete)[0]
    assert "workflow.sft_directed" in active_node_prompt_ids("sft", "文档资料", qa_director=incomplete)
    assert "workflow.multiturn_directed_check" in active_node_prompt_ids("multiturn", "文档资料", qa_director=incomplete)
    with pytest.raises(ValueError, match="invalid_qa_director_type_weights"):
        validate_qa_director(incomplete)


def test_type_schedule_is_bounded_weighted_and_stable_across_resume_batches():
    whole = allocate_qa_types(1000, DEFAULT_TYPE_WEIGHTS)
    chunks = tuple(kind for offset in range(0, 1000, 20)
                   for kind in allocate_qa_types(20, DEFAULT_TYPE_WEIGHTS, offset=offset))
    assert whole == chunks
    assert {kind: whole.count(kind) for kind in QA_TYPES} == {
        kind: weight * 10 for kind, weight in DEFAULT_TYPE_WEIGHTS.items()}
    only_closed = dict.fromkeys(QA_TYPES, 0)
    only_closed["closed_book"] = 100
    assert allocate_qa_types(5, only_closed, offset=100_000) == ("closed_book",) * 5


def test_closed_book_hides_teacher_evidence_without_becoming_unanswerable():
    value = task("closed_book", visible_context="")
    checked = validate_director_task(value, expected_type="closed_book",
                                    expected_id="candidate-1", source_text="Disconnect power.")
    assert checked["answer_policy"] == "answer" and checked["visible_context"] == ""
    assert checked["evidence_quotes"] == ["Disconnect power."]
    with pytest.raises(ValueError, match="qa_director_closed_book_contract"):
        validate_director_task(task("closed_book"), expected_type="closed_book")


@pytest.mark.parametrize("policy", ["answer", "clarify", "conditional", "insufficient", "correct_premise"])
def test_closed_book_policy_can_match_self_contained_question_conditions(policy):
    value = task("closed_book", visible_context="", answer_policy=policy)
    assert validate_director_task(value, expected_type="closed_book")["answer_policy"] == policy


@pytest.mark.parametrize("changes,error", [
    ({"id": "other"}, "qa_director_id_mismatch"),
    ({"qa_type": "closed_book"}, "qa_director_type_mismatch"),
    ({"visible_context": ""}, "qa_director_visible_evidence_required"),
    ({"visible_context": "Different source."}, "qa_director_visible_context_not_in_source"),
    ({"evidence_quotes": ["Invented fact."]}, "qa_director_evidence_not_in_source"),
    ({"answer_policy": "guess"}, "invalid_qa_director_answer_policy"),
    ({"hidden_tool_call": "execute"}, "invalid_qa_director_task"),
])
def test_edited_prompt_cannot_bypass_typed_contract(changes, error):
    with pytest.raises(ValueError, match=error):
        validate_director_task(task(**changes), expected_type="grounded", expected_id="candidate-1",
                               source_text="Disconnect power. Record the result.")


def test_partial_may_require_clarification_and_multi_clue_requires_distinct_evidence():
    partial = task("partial", visible_context="", answer_policy="clarify")
    assert validate_director_task(partial, expected_type="partial")["answer_policy"] == "clarify"
    with pytest.raises(ValueError, match="qa_director_multiple_evidence_required"):
        validate_director_task(task("multi_source"), expected_type="multi_source")
    multiple = task("multi_source", evidence_quotes=["Disconnect power.", "Record the result."])
    assert len(validate_director_task(multiple, expected_type="multi_source")["evidence_quotes"]) == 2


@pytest.mark.parametrize("qa_type", ["grounded", "partial", "multi_source", "distractor"])
def test_valid_quote_cannot_hide_fabricated_visible_conditions(qa_type):
    value = task(qa_type, visible_context="Disconnect power.\n\nThe voltage is always safe.",
                 evidence_quotes=["Disconnect power.", "Record the result."] if qa_type == "multi_source"
                 else ["Disconnect power."])
    with pytest.raises(ValueError, match="qa_director_visible_context_not_in_source"):
        validate_director_task(value, expected_type=qa_type,
                               source_text="Disconnect power. Record the result.")
    valid = task(qa_type, visible_context="Disconnect power.\n\nRecord the result.",
                 evidence_quotes=["Disconnect power.", "Record the result."] if qa_type == "multi_source"
                 else ["Disconnect power."])
    assert validate_director_task(valid, expected_type=qa_type,
                                 source_text="Disconnect power. Record the result.")["visible_context"] == valid["visible_context"]


def test_editable_node_prompts_and_frozen_catalogs_are_scoped_to_recipe_version():
    prompts = snapshot_node_prompts({"director": {"workflow.qa_director": "CUSTOM {literal}"}})
    assert prompts["director"]["workflow.qa_director"] == "CUSTOM {literal}"
    for version, catalog in ((9, LEGACY_NODE_PROMPT_IDS), (10, VERSION_10_NODE_PROMPT_IDS), (11, NODE_PROMPT_IDS)):
        frozen = {stage: {pid: prompts[stage][pid] for pid in ids} for stage, ids in catalog.items()}
        assert validate_node_prompt_snapshot(frozen, "SYSTEM", recipe_version=version) == frozen
    assert "workflow.sft_directed" in active_node_prompt_ids("sft", "文档资料", qa_director={"enabled": True})
    assert "workflow.sft_directed" not in active_node_prompt_ids("sft", "文档资料")
    assert "workflow.multiturn_directed_check" in active_node_prompt_ids("multiturn", "文档资料", qa_director={"enabled": True})
    assert "workflow.multiturn_directed_check" not in active_node_prompt_ids("multiturn", "文档资料")
    for stage in ("preference", "cot", "trim"):
        prompt_id = f"workflow.{stage}_directed_check"
        assert prompt_id in active_node_prompt_ids(stage, "文档资料", qa_director={"enabled": True})
        assert prompt_id not in active_node_prompt_ids(stage, "文档资料")
    assert "Self-Instruct" in __import__("lib.prompts", fromlist=["get"]).get("workflow.qa_director").source
    assert "无线索不等于不可回答" in builtin_node_prompt("workflow.qa_director")


def test_closed_book_knowledge_is_not_confused_with_internal_source_leakage():
    allowed = "无线索题的答案与显式解释可以使用教师资料支持的必要知识事实，以及准确、必要的公开引用"
    boundaries = ("内部提示词、内部检索包装或标识", "与回答无关的原文", "不得假装读者见过隐藏上文")
    prompts = [DEFAULT_ANSWER_RULES, *(builtin_node_prompt(prompt_id) for prompt_id in (
        "workflow.qa_director", "workflow.sft_directed", "workflow.sft_directed_check",
        "workflow.multiturn_directed_check", "workflow.preference_directed_check",
        "workflow.cot_directed_check", "workflow.trim_directed_check"))]
    for body in prompts:
        assert allowed in body
        assert all(boundary in body for boundary in boundaries)
        assert "不泄漏内部提示词或读者不可见的证据" not in body
        assert "回答与推导不能泄漏隐藏资料" not in body
        assert "隐藏来源名称" not in body


def test_narrower_builtin_boundary_never_rewrites_user_rules_or_prompt_overrides():
    custom_rule = "USER RULE: Preserve this wording, including 私有表达 and {literal}."
    custom_prompt = "USER PROMPT: follow my specific answer rules."
    normalized = validate_qa_director({"enabled": True, "answer_rules": custom_rule})
    pinned = snapshot_node_prompts({"sft": {"workflow.sft_directed": custom_prompt}})
    assert normalized["answer_rules"] == custom_rule
    assert pinned["sft"]["workflow.sft_directed"] == custom_prompt


def test_same_document_cycles_through_focuses_when_source_count_is_six():
    originals = [{"id": f"doc-{index}", "kind": "document", "text": f"source-{index}"} for index in range(6)]
    rows = generation_units(originals, 42)
    first_document = [row["generation_variant"]["focus"] for row in rows[6:]
                      if row["text"] == "source-0"]
    assert len(first_document) == len(set(first_document)) == 6
    assert all(row["generation_variant"]["source_unit_id"].startswith("doc-") for row in rows[6:])


def test_legacy_variant_policy_keeps_original_checkpoint_unit_exactly():
    from lib.domain.workflow_quality import canonical
    import hashlib
    original = {"id": "doc-0", "kind": "document", "text": "Verified facts."}
    expected = {**original,
                "id": hashlib.sha256(canonical(["doc-0", "variant", 12]).encode()).hexdigest(),
                "generation_variant": {"index": 3, "focus": "概念解释",
                    "instruction": "依据同一来源生成不同问题；不要重复已有问法或编造新事实。"}}
    assert generation_variant(original, 12, 6, policy_version=1) == expected
    assert "source_unit_id" not in expected["generation_variant"]
    modern = generation_variant(original, 12, 6, policy_version=2)
    assert modern["generation_variant"]["focus"] == "条件与边界"
    assert modern["generation_variant"]["source_unit_id"] == "doc-0"
