"""Dedicated editor protocol, atomic patches and explicit non-approval."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from lib.contexts.workflows.domain.knowledge_reasoning_editor import (
    EDIT_KNOWLEDGE_REASONING, EDIT_KNOWLEDGE_REASONING_SEGMENTS,
    apply_edits, apply_local_edits, knowledge_reasoning_editor_tool, apply_segment_edits,
    reasoning_segments, knowledge_reasoning_segment_editor_tool, prepare_self_edit_messages,
)
from lib.contexts.workflows.domain.dialogue import dialogue_digest


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def submission(status, edits=(), issues=()):
    return {"role": "assistant", "content": "", "tool_calls": [{"id": "editor-1", "type": "function",
        "function": {"name": EDIT_KNOWLEDGE_REASONING,
            "arguments": json.dumps({"status": status, "edits": list(edits), "issues": list(issues)}, ensure_ascii=False)}}]}


def segment_submission(status, edits=(), issues=()):
    assistant = submission(status, edits, issues)
    assistant["tool_calls"][0]["function"]["name"] = EDIT_KNOWLEDGE_REASONING_SEGMENTS
    return assistant


def test_local_reference_edits_preserve_other_reasoning_conditions_and_raw_hashes():
    original = "  据材料，分类先确定标签。\n目标类别不保证标签正确；未知条件仍未确定。\n根据材料，摘要先生成输入。  "
    assistant = submission("edited", [
        {"old_text": "根据材料，摘要", "new_text": "摘要"},
        {"old_text": "据材料，分类", "new_text": "分类"}])
    before = deepcopy(assistant)
    derived, receipt = apply_edits(original, assistant)
    assert derived == "  分类先确定标签。\n目标类别不保证标签正确；未知条件仍未确定。\n摘要先生成输入。  "
    assert receipt["original_sha256"] == sha256(original.encode("utf-8")).hexdigest()
    assert receipt["derived_sha256"] == sha256(derived.encode("utf-8")).hexdigest()
    assert receipt["changed"] and receipt["semantic_status"] == "not_assessed"
    assert assistant == before
    tool = knowledge_reasoning_editor_tool()
    assert tool["function"]["strict"] is True
    schema = tool["function"]["parameters"]
    assert set(schema["required"]) == {"status", "edits", "issues"}
    assert schema["additionalProperties"] is False
    assert "reasoning_trace" not in repr(schema) and "answer" not in schema["properties"]
    assert all(name not in repr(schema) for name in ("minLength", "maxLength", "minItems", "maxItems"))


def test_unchanged_and_safe_edits_with_content_issues_never_claim_quality_approval():
    original = "根据材料，A只能导致B；必要条件尚未明确。"
    unchanged, receipt = apply_edits(original, submission("unchanged"))
    assert unchanged == original and not receipt["changed"]
    derived, pending = apply_edits(original, submission("needs_content_repair",
        [{"old_text": "根据材料，", "new_text": ""}],
        [{"quote": "A只能导致B", "reason": "唯一性未被证明，不能用措辞清理替代事实修复。"}]))
    assert derived == "A只能导致B；必要条件尚未明确。"
    assert pending["status"] == "needs_content_repair" and pending["content_repair_required"]
    assert pending["semantic_status"] == receipt["semantic_status"] == "not_assessed"
    with pytest.raises(ValueError, match="status_mismatch"):
        apply_edits(original, submission("unchanged", [{"old_text": "根据材料，", "new_text": ""}]))


def test_edit_targets_must_be_current_unique_and_disjoint_before_any_replacement():
    original = "abcde abcde xy"
    cases = [
        ([{"old_text": "abcde", "new_text": "A"}], "ambiguous_edit"),
        ([{"old_text": "missing", "new_text": "A"}], "stale_edit"),
        ([{"old_text": "de xy", "new_text": "A"}, {"old_text": "xy", "new_text": "B"}], "overlapping_edits"),
        ([{"old_text": "xy", "new_text": "xy"}], "unchanged_edit"),
    ]
    for edits, error in cases:
        assistant = submission("edited", edits)
        before = deepcopy(assistant)
        with pytest.raises(ValueError, match=error):
            apply_edits(original, assistant)
        assert assistant == before
    with pytest.raises(ValueError, match="ambiguous_edit"):
        apply_edits("aaa", submission("edited", [{"old_text": "aa", "new_text": "b"}]))


def test_closed_single_tool_submission_rejects_prose_extra_fields_and_duplicate_json_keys():
    original = "Keep this original reasoning."
    for invalid in (
        {**submission("unchanged"), "content": "Here is my edited text."},
        {**submission("unchanged"), "tool_calls": submission("unchanged")["tool_calls"] * 2},
    ):
        with pytest.raises(ValueError, match="invalid_submission"):
            apply_edits(original, invalid)
    assistant = submission("unchanged")
    for raw, error in (
        ('{"status":"unchanged","edits":[],"issues":[],"answer":"changed"}', "invalid_schema"),
        ('{"status":"unchanged","status":"edited","edits":[],"issues":[]}', "duplicate_field"),
        ('{"status":"unchanged","edits":[],"issues":[]}}', "invalid_json"),
    ):
        assistant["tool_calls"][0]["function"]["arguments"] = raw
        with pytest.raises(ValueError, match=error):
            apply_edits(original, assistant)


def test_empty_reasoning_invalid_literals_and_unbound_issue_cannot_replace_original():
    original = "完整推导保留。"
    for assistant, error in (
        (submission("edited", [{"old_text": original, "new_text": ""}]), "empty_reasoning"),
        (submission("edited", [{"old_text": "", "new_text": "新内容"}]), "invalid_schema"),
        (submission("edited", [{"old_text": "推导", "new_text": "\ud800"}]), "invalid_text"),
        (submission("needs_content_repair", issues=[{"quote": "不存在的引文", "reason": "未定位"}]), "unbound_issue"),
    ):
        with pytest.raises(ValueError, match=error):
            apply_edits(original, assistant)
    answer = "A只能导致B。"
    issue = submission("needs_content_repair", issues=[{"quote": "A只能导致B", "reason": "最终答案中的唯一性尚未成立。"}])
    with pytest.raises(ValueError, match="unbound_issue"):
        apply_edits(original, issue)
    derived, pending = apply_edits(original, issue, final_answer=answer)
    assert derived == original and pending["content_repair_required"]
    assert pending["fixed_answer_sha256"] == sha256(answer.encode("utf-8")).hexdigest()
    assert answer == "A只能导致B。" and pending["semantic_status"] == "not_assessed"


def test_segment_ids_edit_repeated_paragraphs_while_host_preserves_all_separators():
    original = "\n\n相同段落。\r\n保留条件。\r\n \t\r\n相同段落。\r\n保留条件。\n\n\n末段。\r\n"
    segments = reasoning_segments(original)
    assert len(segments) == 3 and segments[0]["text"] == segments[1]["text"]
    assert len({part["segment_id"] for part in segments}) == 3
    assert all(original[part["start"]:part["end"]] == part["text"] for part in segments)
    replacement = "独立事实。\n保留条件。"
    assistant = segment_submission("edited", [{"segment_id": segments[1]["segment_id"], "new_text": replacement}])
    before = deepcopy(assistant)
    derived, receipt = apply_segment_edits(original, assistant, final_answer="固定答案。")
    assert derived == "\n\n相同段落。\r\n保留条件。\r\n \t\r\n独立事实。\n保留条件。\n\n\n末段。\r\n"
    assert receipt["version"] == 2 and receipt["semantic_status"] == "not_assessed"
    assert receipt["edits"] == [{"segment_id": segments[1]["segment_id"],
        "old_text": "相同段落。\r\n保留条件。", "new_text": replacement}]
    assert assistant == before
    schema = knowledge_reasoning_segment_editor_tool(segments)["function"]["parameters"]
    edit_schema = schema["properties"]["edits"]["items"]
    assert set(edit_schema["properties"]) == {"segment_id", "new_text"}
    assert edit_schema["properties"]["segment_id"]["enum"] == [part["segment_id"] for part in segments]
    unchanged, receipt = apply_segment_edits(original, segment_submission("unchanged"))
    assert unchanged == original and not receipt["changed"]


def test_duplicate_unknown_and_stale_segment_ids_cannot_partially_apply():
    original = "第一段。\n\n第二段。"
    first = reasoning_segments(original)[0]["segment_id"]
    stale = reasoning_segments(original + "\n")[0]["segment_id"]
    for edits, error in (
        ([{"segment_id": first, "new_text": "A"}, {"segment_id": first, "new_text": "B"}], "duplicate_segment"),
        ([{"segment_id": first, "new_text": "A"}, {"segment_id": "unknown", "new_text": "B"}], "invalid_schema"),
        ([{"segment_id": stale, "new_text": "A"}], "invalid_schema"),
        ([{"segment_id": first, "new_text": "第一段。"}], "unchanged_edit"),
    ):
        assistant = segment_submission("edited", edits)
        before = deepcopy(assistant)
        with pytest.raises(ValueError, match=error):
            apply_segment_edits(original, assistant)
        assert assistant == before


def test_self_edit_preparation_preserves_actual_prefix_native_channels_and_roles():
    prefix = [{"role": "system", "content": "Frozen instructions."},
        {"role": "system", "content": "Exact teacher excerpt.\r\n"},
        {"role": "user", "content": "Which generation order fits these two batches?"}]
    actual = {"role": "assistant", "content": "固定原答案。",
        "reasoning_content": "  完整原生推理。\r\n保留每一行。\n"}
    instruction = "应用要求只提交原推理的编辑补丁，原答案保持不变。"
    before = deepcopy((prefix, actual))
    messages, receipt = prepare_self_edit_messages(prefix, actual, instruction)
    assert messages[:len(prefix)] == prefix
    assert messages[-2:] == [actual, {"role": "system", "content": instruction}]
    assert sum(message["role"] == "user" for message in messages) == 1
    assert all("tool_calls" not in message for message in messages)
    assert receipt["original_reasoning_sha256"] == sha256(actual["reasoning_content"].encode("utf-8")).hexdigest()
    assert receipt["fixed_answer_sha256"] == sha256(actual["content"].encode("utf-8")).hexdigest()
    assert receipt["reasoning_scope"] == "full_original_reasoning_content"
    assert receipt["provenance_scope"] == "message_and_channel_hashes_only"
    assert receipt["semantic_status"] == "not_assessed" and "author" not in receipt
    assert (prefix, actual) == before
    messages[0]["content"] = "local edit"
    messages[-2]["reasoning_content"] = "local edit"
    assert (prefix, actual) == before


def test_self_edit_rejects_missing_historical_thoughts_without_filling_synthetic_history():
    prefix = [{"role": "system", "content": "Frozen instructions."},
        {"role": "assistant", "content": "Synthetic teacher text has no native history."},
        {"role": "user", "content": "Explain the method."}]
    actual = {"role": "assistant", "content": "原答案。", "reasoning_content": "真实原生推理。"}
    before = deepcopy((prefix, actual))
    with pytest.raises(ValueError, match="history_reasoning_missing"):
        prepare_self_edit_messages(prefix, actual, "Edit only the existing reasoning.")
    assert "reasoning_content" not in prefix[1] and (prefix, actual) == before
    with pytest.raises(ValueError, match="native_feedback_reasoning_missing"):
        prepare_self_edit_messages([prefix[0], prefix[-1]], {"role": "assistant", "content": "只有答案。"}, "Edit.")


def test_local_only_rejects_full_or_split_rewrites_without_changing_legacy_replay():
    original = "\n第一条判断。\r\n \t\r\n第二条判断！\n"
    cases = [
        [{"old_text": original, "new_text": "完整新稿。"}],
        [{"old_text": original.strip(), "new_text": "仅保留外侧空白的新稿。"}],
        [{"old_text": "第一条判断", "new_text": "分类先选标签"},
         {"old_text": "第二条判断", "new_text": "摘要先有原文"}],
    ]
    for edits in cases:
        assistant = submission("edited", edits)
        before = deepcopy(assistant)
        with pytest.raises(ValueError, match="full_rewrite_forbidden"):
            apply_local_edits(original, assistant)
        legacy, receipt = apply_edits(original, assistant)
        assert legacy != original and receipt["version"] == 1
        assert assistant == before
    segments = reasoning_segments(original)
    legacy, receipt = apply_segment_edits(original, segment_submission("edited", [
        {"segment_id": part["segment_id"], "new_text": f"新段落{index}。"}
        for index, part in enumerate(segments)]))
    assert legacy != original and receipt["version"] == 2


def test_local_only_allows_scattered_phrase_edits_and_keeps_unchanged_or_pending_status():
    original = "  据材料，分类先选标签。\r\n\r\n根据文本，摘要先有原文。\n"
    edits = [{"old_text": "据材料，", "new_text": ""}, {"old_text": "根据文本，", "new_text": ""}]
    answer = "固定原答案。"
    for status, patches, issues in (
        ("edited", edits, []),
        ("unchanged", [], []),
        ("needs_content_repair", edits, [{"quote": "固定原答案", "reason": "固定答案仍需核对。"}]),
    ):
        assistant = submission(status, patches, issues)
        before = deepcopy(assistant)
        derived, receipt = apply_local_edits(original, assistant, final_answer=answer)
        assert derived == (original if not patches else "  分类先选标签。\r\n\r\n摘要先有原文。\n")
        assert receipt["version"] == 3 and receipt["application"] == "atomic_local_replacements"
        assert receipt["sha256"] == dialogue_digest({key: value for key, value in receipt.items() if key != "sha256"})
        assert receipt["original_sha256"] == sha256(original.encode("utf-8")).hexdigest()
        assert receipt["status"] == status and receipt["content_repair_required"] == (status == "needs_content_repair")
        assert receipt["semantic_status"] == "not_assessed" and assistant == before
