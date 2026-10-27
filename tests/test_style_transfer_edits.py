"""Versioned local restyling through real SDK framing and saved author sessions."""
from copy import deepcopy
import json

import pytest

from lib.bootstrap.node_generation import authored_submission_repairer, cot_generator
from lib.contexts.workflows.application.generate_cot import GenerateCot
from lib.contexts.workflows.application.generate_with_sources import GenerateWithSources
from lib.contexts.workflows.domain.authored_edits import EDIT_AUTHORED
from lib.contexts.workflows.domain.reasoning_checklist import checklist_digest
from model_streaming_fixtures import sdk_client, sse_response
from test_source_memory_requests import workflow
from test_strict_authored_submission import derived_candidate, stream_bytes
from test_cot_style_transfer import replacement
from test_integrated_reflection import recipe
from test_generation_node_use_cases import MemoryGenerationPort, conversation, score


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def edit_response(base, old, new):
    return {"role": "assistant", "content": "", "tool_calls": [{"id": "edit-restyle", "type": "function",
        "function": {"name": EDIT_AUTHORED, "arguments": json.dumps({
            "base_sha256": checklist_digest(base),
            "edits": [{"field": "reasoning", "old_text": old, "new_text": new}],
            "reasoning_trace": derived_candidate()["reasoning_trace"]}, ensure_ascii=False)}}]}


@pytest.mark.parametrize("lookup", [False, True])
def test_existing_trace_is_patched_and_can_continue_in_same_author_session(tmp_path, lookup):
    from test_generation_source_lookup import LOOKUP, paper, tool
    base = {"reasoning": derived_candidate()["reasoning"]}
    revised = {"reasoning": base["reasoning"].replace("先移去等式右侧的3", "我先将等式两侧同时减3"),
               "reasoning_trace": derived_candidate()["reasoning_trace"]}
    replies = ([tool()] if lookup else []) + [
        edit_response(base, "先移去等式右侧的3", "我先将等式两侧同时减3"),
        edit_response(revised, "再将两侧除以2", "再将等式两侧除以非零的2")]
    captured = []
    def handle(request):
        captured.append(json.loads(request.content))
        return sse_response(stream_bytes(replies[len(captured) - 1]))
    client = sdk_client("chat", handle)
    worker = workflow(tmp_path, client, node_generation=recipe("faithful_restyle")["node_generation"])
    worker.stage = "cot"
    worker.recipe.update(authored_submission_version=1, authored_stage_contract_version=1,
        authored_local_edit_version=1, cot_style_transfer_version=2,
        node_generation={"cot": {"answer_mode": "faithful_restyle"}})
    task = {"prompt": [{"role": "user", "content": "求解2x+3=13。"}], "source": paper(),
            "style_transfer": {"original": {**base, "answer": "x=5。"}}}
    before = deepcopy(task)
    try:
        if lookup:
            result, receipt = GenerateWithSources(cot_generator(worker)._port, LOOKUP,
                strict_submission=True, local_edit=True, edit_base=base).execute(
                    ["style-source"], "workflow.cot_rationale", task, "保留论证。")
            assert receipt["status"] == "completed" and receipt["transcript"][-1]["applied_patch"]
            key = receipt["submission_key"]
        else:
            key = ["style-direct"]
            result = worker.ask(key, "generation", "workflow.cot_rationale", task)
            assert worker.ask(key, "generation", "workflow.cot_rationale", task) == result
            assert len(captured) == 1
        assert result == revised and task == before
        assert captured[-1]["tools"][-1]["function"]["name"] == EDIT_AUTHORED
        assert "answer" not in captured[-1]["tools"][-1]["function"]["parameters"]["properties"]
        assert any(checklist_digest(base) in m.get("content", "") for m in captured[-1]["messages"] if m["role"] == "system")
        candidate, receipt = authored_submission_repairer(worker).edit(["continue-style"], key,
            findings=[{"quote": "再将两侧除以2", "issue": "补充为何允许除以该系数。"}])
        assert candidate["reasoning"] == revised["reasoning"].replace("再将两侧除以2", "再将等式两侧除以非零的2")
        assert receipt["tools_preserved"] and receipt["message_prefix_preserved"]
        assert receipt["semantic_status"] == "not_assessed"
        assert captured[-1]["messages"][:-2] == captured[-2]["messages"]
        assert captured[-1]["messages"][-2]["reasoning_content"] == "原生思考\nNative reasoning."
        assert len(captured) == (3 if lookup else 2)
    finally:
        client.client.close()


def test_reviewer_uses_author_scope_without_removing_real_history_or_original_trace():
    from test_authored_task_contract import task, HIDDEN, EXCERPT
    class Port(MemoryGenerationPort):
        def assess(self, key, context, candidate, **options):
            self.review_context = context
            self.calls.append((key, "assessment", candidate))
            return score()
    sample = conversation()
    selected = task()
    sample["messages"] = selected["prompt"] + sample["messages"][-1:]
    sample["source_context"] = selected["source"]
    before = deepcopy(sample)
    config = recipe("faithful_restyle")
    config.update(cot_style_transfer_version=2, authored_submission_version=1)
    port = Port([replacement()])
    row, = GenerateCot(port, config).generate_styled(sample, {"instruction": "保持事实边界。"})
    author, review = port.calls[0][3], port.review_context
    assert HIDDEN not in json.dumps(author) and HIDDEN not in json.dumps(review)
    assert author["source"]["text"] == review["source"]["text"] == EXCERPT
    assert author["prompt"] == review["prompt"]
    assert author["style_transfer"]["original"] == review["style_transfer"]["original"]
    assert row["style_transfer"]["answer_preserved"] and sample == before
    assert row["style_transfer"]["editing_protocol"] == "literal_replacements_v2"


def test_failed_literal_patch_gets_one_real_tool_feedback_turn_without_fuzzy_matching(tmp_path):
    base = {"reasoning": derived_candidate()["reasoning"]}
    bad = edit_response(base, "先移去右侧的3", "我先将等式两侧同时减3")
    good = edit_response(base, "先移去等式右侧的3", "我先将等式两侧同时减3")
    captured = []
    def handle(request):
        captured.append(json.loads(request.content))
        return sse_response(stream_bytes(bad if len(captured) == 1 else good))
    client = sdk_client("chat", handle)
    worker = workflow(tmp_path, client, node_generation=recipe("faithful_restyle")["node_generation"])
    worker.stage = "cot"
    worker.recipe.update(authored_submission_version=1, authored_stage_contract_version=1,
        authored_local_edit_version=1, cot_style_transfer_version=2)
    task = {"prompt": [{"role": "user", "content": "求解2x+3=13。"}],
            "style_transfer": {"original": {**base, "answer": "x=5。"}}}
    try:
        with pytest.raises(ValueError, match="target_not_unique"):
            worker.ask(["bad-style"], "generation", "workflow.cot_rationale", task)
        recovery = authored_submission_repairer(worker)
        candidate, receipt = recovery.recover_edit(["repair-target"], ["bad-style"], anchors=["先移去等式右侧的3"])
        assert candidate["reasoning"] == base["reasoning"].replace("先移去等式右侧的3", "我先将等式两侧同时减3")
        assert receipt["original_assistant"]["tool_calls"] == bad["tool_calls"]
        assert receipt["observation"]["applied_edits"] == 0 and receipt["semantic_status"] == "not_assessed"
        assert captured[1]["messages"][:-2] == captured[0]["messages"]
        assert captured[1]["tools"] == captured[0]["tools"]
        assert recovery.recover_edit(["repair-target"], ["bad-style"], anchors=["先移去等式右侧的3"]) == (candidate, receipt)
        assert len(captured) == 2
        with pytest.raises(ValueError, match="unbound_anchor"):
            recovery.recover_edit(["bad-anchor"], ["bad-style"], anchors=["invented original text"])
        assert len(captured) == 2
    finally:
        client.client.close()
