"""Same-author patching through production checkpoints and real SDK framing."""
from copy import deepcopy
import json

import pytest

from lib.bootstrap.node_generation import authored_submission_repairer
from lib.contexts.workflows.domain.authored_edits import EDIT_AUTHORED, apply_authored_edits
from lib.contexts.workflows.domain.reasoning_checklist import checklist_digest
from model_streaming_fixtures import sdk_client, sse_response
from test_source_memory_requests import workflow
from test_strict_authored_submission import derived_candidate, submission, stream_bytes


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def wrong_candidate():
    value = derived_candidate()
    value["answer"] = "x=6。"
    return value


def edit_reply(candidate=None):
    candidate = candidate or wrong_candidate()
    return {"role": "assistant", "content": "", "tool_calls": [
        {"id": "edit-1", "type": "function", "function": {"name": EDIT_AUTHORED,
         "arguments": json.dumps({"base_sha256": checklist_digest(candidate),
             "edits": [{"field": "answer", "old_text": "x=6。", "new_text": "x=5。"}],
             "reasoning_trace": candidate["reasoning_trace"]}, ensure_ascii=False)}}]}


def setup(tmp_path, *, registered=True, invalid_reply=False):
    captured = []
    reply = edit_reply()
    if invalid_reply:
        payload = json.loads(reply["tool_calls"][0]["function"]["arguments"])
        payload["base_sha256"] = "stale"
        reply["tool_calls"][0]["function"]["arguments"] = json.dumps(payload)

    def handle(request):
        captured.append(json.loads(request.content))
        return sse_response(stream_bytes(submission(wrong_candidate()) if len(captured) == 1 else reply))

    client = sdk_client("chat", handle)
    worker = workflow(tmp_path, client)
    worker.stage = "cot"
    worker.recipe.update(version=36, authored_submission_version=1, authored_stage_contract_version=1)
    if registered:
        worker.recipe["authored_local_edit_version"] = 1
    worker.ask(["original"], "generation", "workflow.cot_generate",
               {"learner_messages": [{"role": "user", "content": "求解2x+3=13。"}]})
    return worker, client, captured


def edit(worker):
    return authored_submission_repairer(worker).edit(["correction"], ["original"],
        findings=[{"quote": "x=6。", "issue": "与原方程及正文验算是否一致？"}])


def test_patch_reuses_real_author_wire_prefix_tools_and_native_history_without_rebuying(tmp_path):
    worker, client, captured = setup(tmp_path)
    try:
        candidate, receipt = edit(worker)
        assert candidate == derived_candidate()
        original, revised = captured
        assert revised["model"] == original["model"]
        assert revised["tools"] == original["tools"]
        assert revised["messages"][:-2] == original["messages"]
        assert any("进入编辑阶段" in m.get("content", "") for m in original["messages"] if m["role"] == "system")
        assert revised["messages"][-2]["reasoning_content"] == "原生思考\nNative reasoning."
        assert revised["messages"][-1]["role"] == "tool"
        assert revised["messages"][-1]["tool_call_id"] == revised["messages"][-2]["tool_calls"][0]["id"]
        assert receipt["semantic_status"] == "not_assessed"
        assert receipt["tools_preserved"] and receipt["message_prefix_preserved"]
        assert receipt["original_candidate"] == wrong_candidate()
        assert candidate["reasoning"] == wrong_candidate()["reasoning"]
        assert receipt["response_details"]["strict_tool_response"]["usage"]["prompt_cache_hit_tokens"] == 100
        assert edit(worker) == (candidate, receipt)
        assert len(captured) == 2
    finally:
        client.client.close()


@pytest.mark.parametrize("change", ["model", "credential", "reasoning", "node_binding"])
def test_changed_author_cannot_route_edit_to_another_model_or_account(tmp_path, change):
    worker, client, captured = setup(tmp_path)
    try:
        if change == "model":
            client.model = "another-model"
        elif change == "credential":
            client.client.api_key = "another-test-account"
        elif change == "reasoning":
            client.reasoning = {"mode": "disabled"}
        else:
            worker.recipe.setdefault("node_models", {}).setdefault("cot", {})["generation"] = {"max_output_tokens": 1024}
        with pytest.raises(ValueError, match="model_configuration_changed|original_model_changed"):
            edit(worker)
        assert len(captured) == 1
    finally:
        client.client.close()


def test_earlier_tool_set_is_reported_as_changed_without_rewriting_message_prefix(tmp_path):
    worker, client, captured = setup(tmp_path, registered=False)
    try:
        _, receipt = edit(worker)
        assert not receipt["tools_preserved"] and receipt["message_prefix_preserved"]
        assert captured[1]["tools"][:-1] == captured[0]["tools"]
        assert captured[1]["messages"][:-3] == captured[0]["messages"]
        assert captured[1]["messages"][-1]["role"] == "system"
    finally:
        client.client.close()


def test_bad_completed_patch_is_preserved_and_never_rebought(tmp_path):
    worker, client, captured = setup(tmp_path, invalid_reply=True)
    try:
        for _ in range(2):
            with pytest.raises(ValueError, match="stale_candidate"):
                edit(worker)
        assert len(captured) == 2
    finally:
        client.client.close()


def test_source_tool_conversation_retains_its_actual_author_submission_and_prefix(tmp_path):
    from lib.bootstrap.node_generation import cot_generator
    from lib.contexts.workflows.application.generate_with_sources import GenerateWithSources
    from test_generation_source_lookup import LOOKUP, paper, tool

    captured = []
    replies = [tool(), submission(wrong_candidate()), edit_reply()]

    def handle(request):
        captured.append(json.loads(request.content))
        return sse_response(stream_bytes(replies[len(captured) - 1]))

    client = sdk_client("chat", handle)
    worker = workflow(tmp_path, client)
    worker.stage = "cot"
    worker.recipe.update(version=36, authored_submission_version=1, authored_stage_contract_version=1,
                         authored_local_edit_version=1)
    try:
        _, source_receipt = GenerateWithSources(cot_generator(worker)._port, LOOKUP,
            strict_submission=True, local_edit=True).execute(["source-case"], "workflow.cot_generate",
                {"source": paper(), "prompt": [{"role": "user", "content": "求解2x+3=13。"}]}, "保留来源范围。")
        assert source_receipt["status"] == "completed"
        candidate, receipt = authored_submission_repairer(worker).edit(["source-edit"], source_receipt["submission_key"],
            findings=[{"quote": "x=6。", "issue": "与原式是否一致？"}])
        assert candidate == derived_candidate() and receipt["tools_preserved"]
        assert captured[2]["messages"][:-2] == captured[1]["messages"]
        assert captured[2]["tools"] == captured[1]["tools"]
        assert any(m["role"] == "tool" and m.get("tool_call_id") == "a" for m in captured[2]["messages"][:-2])
        assert len(captured) == 3
    finally:
        client.client.close()


@pytest.mark.parametrize("fault", ["ambiguous", "delete_reasoning", "empty_checks", "wrong_field"])
def test_invalid_edits_are_atomic_and_keep_full_validation(fault):
    candidate = wrong_candidate()
    reply = edit_reply(candidate)
    patch = json.loads(reply["tool_calls"][0]["function"]["arguments"])
    if fault == "ambiguous":
        patch["edits"].append({"field": "reasoning", "old_text": "2", "new_text": "3"})
    elif fault == "delete_reasoning":
        patch["edits"].append({"field": "reasoning", "old_text": candidate["reasoning"], "new_text": ""})
    elif fault == "empty_checks":
        patch["reasoning_trace"]["checks"] = []
    else:
        patch["edits"][0]["field"] = "user"
    reply["tool_calls"][0]["function"]["arguments"] = json.dumps(patch)
    before = deepcopy(candidate)
    with pytest.raises(ValueError):
        apply_authored_edits(candidate, reply, answer_required=True)
    assert candidate == before
