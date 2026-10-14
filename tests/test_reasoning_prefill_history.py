"""Edits cannot attribute a new answer to an earlier native prefill call."""
from copy import deepcopy
import hashlib

import pytest

from lib.domain.workflow_quality import canonical
from lib.domain.workflow_targets import TRAINING_FIELDS, training_record
from lib.infrastructure.reasoning_prefill_history import retire_reasoning_prefill
from lib.infrastructure.workflow_review_repair import (
    WorkflowReviewRepair, _manual_patch, preserve_provider_reasoning,
)


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


PREFIX = "\n  这是原样保留的已核验解释。\n"
NATIVE_CONTINUATION = "  provider-only continuation\n"
QUESTION = "请解释这些事实。\\n\\n不要更换原题。"
OLD_ANSWER = "原答案。"


def native_row():
    receipt = {"version": 1, "transport": "deepseek_chat_beta",
               "prefix_sha256": hashlib.sha256(PREFIX.encode()).hexdigest(),
               "prefix_utf8_bytes": len(PREFIX.encode()), "response_reasoning": "provider_continuation"}
    return {"id": "sample", "source_id": "source", "status": "eligible",
            "question": QUESTION, "reasoning": [PREFIX], "answer": OLD_ANSWER,
            "messages": [{"role": "user", "content": QUESTION},
                         {"role": "assistant", "content": OLD_ANSWER, "reasoning_content": PREFIX}],
            "reasoning_origin": "authored_prefix_native_answer",
            "reasoning_prefill": receipt, "prefix_review": {"keep": True, "adherence": 5, "reason": "原前缀合格。"},
            "answer_response_details": {"reasoning_content": NATIVE_CONTINUATION,
                                        "reasoning_prefill": deepcopy(receipt)},
            "reasoning_scope": "authored_prefix_not_complete_provider_internal_reasoning"}


def score():
    return {"keep": True, "grounded": True, "reasoning_valid": True, "correctness": 5,
            "scores": {name: 5 for name in ("correctness", "reasoning", "grounding", "instruction", "safety")},
            "reason": "符合当前任务。"}


class ReviewProbe(WorkflowReviewRepair):
    def __init__(self, repair):
        self.recipe = {"qa_agent_memory_version": 1, "package_review": {}, "generation_preferences": {},
                       "review_repair": {"mode": "auto", "max_rounds": 0}}
        self.state = {"id": "offline-review", "stages": {"review": {}}}
        self.repair, self.calls = repair, []

    def save(self, **options):
        pass

    def _review_payload(self, target, row):
        return {field: deepcopy(row[field]) for field in TRAINING_FIELDS[target]}

    def ask(self, key, role, prompt, data, **options):
        self.calls.append(deepcopy(data))
        return deepcopy(self.repair)


def assert_retired(original, updated, origin):
    assert updated["reasoning_origin"] == origin
    assert updated["reasoning_prefill"] == {
        "version": 1, "applied": False, "status": "not_applied_after_revision",
    }
    assert not {"answer_response_details", "prefix_review", "reasoning_scope"} & set(updated)
    archived, = updated["reasoning_prefill_history"]
    assert archived["reasoning_prefill"] == original["reasoning_prefill"]
    assert archived["answer_response_details"] == original["answer_response_details"]
    assert archived["prefix_review"] == original["prefix_review"]
    assert archived["answer"] == OLD_ANSWER
    assert archived["reasoning"] in ([PREFIX], PREFIX)
    assert archived["superseded_by"] == origin
    assert archived["scope"] == "historical_authored_prefix_native_response_only"
    assert "question" not in archived and "messages" not in archived


@pytest.mark.parametrize("patch", [
    {"answer": "修正答案。"},
    {"corrected_record": {"reasoning": ["人工修改的解释。"]}},
    {"question": "人工明确改写的题目。"},
])
def test_human_edits_archive_receipt_without_erasing_authored_text(patch):
    original = native_row()
    immutable = deepcopy(original)
    updated = _manual_patch("cot", original, patch)
    assert_retired(original, updated, "human_revision")
    assert updated["reasoning"] == patch.get("corrected_record", {}).get("reasoning", [PREFIX])
    assert original == immutable
    assert "reasoning_prefill_history" not in training_record("cot", updated)


def test_metadata_only_scoring_and_noop_edit_keep_current_receipt():
    original = native_row()
    updated = deepcopy(original)
    updated.update(status="eligible", check=score(), score=1.0)
    preserve_provider_reasoning("cot", original, updated, origin="prompt_revision")
    assert updated["reasoning_prefill"] == original["reasoning_prefill"]
    assert updated["reasoning_origin"] == original["reasoning_origin"]
    assert "reasoning_prefill_history" not in updated
    noop = _manual_patch("cot", original, {"answer": OLD_ANSWER})
    assert noop == original


def test_automatic_delta_repair_keeps_original_question_and_retires_old_native_call():
    original = native_row()
    worker = ReviewProbe({"record": {"reasoning": ["独立修正后的解释。"], "answer": "修正答案。"},
                         "quotes": [], "uncertain": False})
    updated, issue = worker._repair_content("cot", original, {"source_kind": "synthetic"},
                                          ["sample"], score(), "修正错误。")
    assert issue is None
    assert updated["question"] == QUESTION
    assert updated["messages"][0]["content"] == QUESTION
    assert updated["reasoning"] == ["独立修正后的解释。"]
    assert updated["answer"] == "修正答案。"
    assert_retired(original, updated, "prompt_revision")
    assert original["reasoning_origin"] == "authored_prefix_native_answer"


def test_automatic_task_rewrite_rejected_before_provenance_changes():
    original = native_row()
    body = training_record("cot", original)
    body["question"] = "偷换的新题。"
    worker = ReviewProbe({"record": body, "quotes": [], "uncertain": False})
    updated, issue = worker._repair_content("cot", original, {"source_kind": "synthetic"},
                                          ["sample"], score(), "修正错误。")
    assert issue == "review_repair_learner_task_changed"
    assert updated is original and "reasoning_prefill_history" not in original


def test_failed_source_validation_does_not_mutate_original_receipt():
    original = native_row()
    worker = ReviewProbe({"record": {"reasoning": ["修正解释。"], "answer": "修正答案。"},
                         "quotes": [], "uncertain": False})
    updated, issue = worker._repair_content("cot", original,
                                          {"source_kind": "document", "teacher_evidence": "独立资料事实。"},
                                          ["sample"], score(), "修正错误。")
    assert issue == "review_repair_source_quotes_missing"
    assert updated is original and "reasoning_prefill_history" not in original


@pytest.mark.parametrize("target", ["cot", "sft"])
def test_trim_invalidates_prefill_claim_without_changing_text_again(target):
    original = native_row()
    if target == "sft":
        for field in ("question", "reasoning", "answer"):
            original.pop(field)
    updated = deepcopy(original)
    if target == "cot":
        updated["reasoning"] = ["修剪后的解释。"]
    updated["messages"][-1]["reasoning_content"] = "修剪后的解释。"
    text_before = deepcopy(training_record(target, updated))
    preserve_provider_reasoning(target, original, updated, origin="prompt_trim")
    assert_retired(original, updated, "prompt_trim")
    assert training_record(target, updated) == text_before


def test_retiring_same_receipt_is_idempotent_and_keeps_existing_private_history():
    original = native_row()
    older = {"scope": "another historical receipt", "reasoning": "旧历史。"}
    original["reasoning_prefill_history"] = [deepcopy(older)]
    updated = deepcopy(original)
    updated["answer"] = "修正答案。"
    retire_reasoning_prefill("cot", original, updated, origin="human_revision")
    first = canonical(updated)
    retire_reasoning_prefill("cot", original, updated, origin="human_revision")
    assert canonical(updated) == first
    assert updated["reasoning_prefill_history"][0] == older
    assert len(updated["reasoning_prefill_history"]) == 2


def test_receipt_without_origin_is_still_retired_but_legacy_rows_are_unchanged():
    original = native_row()
    original.pop("reasoning_origin")
    updated = deepcopy(original)
    updated["answer"] = "修正答案。"
    preserve_provider_reasoning("cot", original, updated, origin="prompt_revision")
    assert_retired(original, updated, "prompt_revision")
    legacy = {"question": QUESTION, "reasoning": [PREFIX], "answer": OLD_ANSWER}
    edited = {**deepcopy(legacy), "answer": "修正答案。"}
    expected = deepcopy(edited)
    preserve_provider_reasoning("cot", legacy, edited, origin="prompt_revision")
    assert edited == expected
