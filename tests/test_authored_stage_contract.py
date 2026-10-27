"""Wire-level stage boundaries, unchanged history and preserved native prefixes."""
from copy import deepcopy
import json

import httpx
import pytest

from lib.contexts.workflows.domain.authored_stage_contract import authored_stage_contract
from lib.contexts.workflows.domain.integrated_reflection import default_integrated_reflection
from test_source_memory_requests import CapturingGenerator, workflow
from test_reasoning_prefill import client_for, full_reply


@pytest.mark.parametrize("prompt_id", ["workflow.cot_generate", "workflow.cot_rationale", "workflow.cot_native_answer"])
def test_new_stage_preserves_exact_legacy_prefix_and_actual_turns(tmp_path, prompt_id):
    writer = CapturingGenerator([{"ok": True}, {"ok": True}])
    worker = workflow(tmp_path, writer, node_generation={"cot": {"enabled": True,
        "self_check": default_integrated_reflection()}})
    assert worker.recipe["version"] == 35
    assert worker.recipe["authored_stage_contract_version"] == 1
    worker.recipe.pop("reflection_guidance_placement_version")
    worker.stage = "cot"
    turns = [{"role": "user", "content": "我没预算重新训练。"},
             {"role": "assistant", "content": "你可以用现有权重评估。"},
             {"role": "user", "content": "我还保留了 base。帮我用第一人称拟一段报告。"}]
    payload = {"learner_messages": turns, "source_context": {"id": "paper", "kind": "document",
        "text": "PRIVATE_SOURCE\n" * 1000}}
    original = deepcopy(payload)
    worker.recipe.pop("authored_stage_contract_version")
    worker.recipe["version"] = 33
    worker.ask(["case", "legacy", prompt_id], "generation", prompt_id, payload)
    worker.recipe.update(version=34, authored_stage_contract_version=1)
    worker.ask(["case", "current", prompt_id], "generation", prompt_id, payload)
    previous, current = writer.calls
    assert current[:-1] == previous
    assert previous[-3:] == turns
    assert current[-1] == {"role": "system", "content": authored_stage_contract(prompt_id)}
    assert payload == original
    worker.ask(["case", "current", prompt_id], "generation", prompt_id, payload)
    assert len(writer.calls) == 2


def test_native_prefix_is_complete_after_answer_contract_in_real_sdk():
    captured = []
    def handler(request):
        captured.append((request.url.path, json.loads(request.content)))
        return httpx.Response(200, json=full_reply("最终答案。", native="完整供应商续写。"))
    client = client_for(handler)
    client.stream_supported = False
    messages = [{"role": "user", "content": "帮我确定报告口径。"},
        {"role": "system", "content": authored_stage_contract("workflow.cot_native_answer")}]
    prefix = "我需要区分建议、代写报告和用户已经完成的操作。\n" * 100
    details = {}
    try:
        assert client.chat(messages, reasoning_prefix=prefix, response_details=details,
            allow_reasoning_fallback=False) == "最终答案。"
        path, sent = captured[0]
        assert path == "/beta/chat/completions"
        assert sent["messages"][:-1] == messages
        assert sent["messages"][-1]["prefix"] is True
        assert sent["messages"][-1]["reasoning_content"] == prefix
        assert "response_format" not in sent
        assert details["reasoning_content"] == "完整供应商续写。"
    finally:
        client.client.close()


def test_contract_does_not_reinterpret_non_json_provider_failure():
    from lib.contexts.models.domain.json_response import JSONResponseDecodeError, parse_json_robust
    with pytest.raises(JSONResponseDecodeError):
        parse_json_robust(" " * 856 + "该差异来自单次训练 run 的观测。")
    with pytest.raises(ValueError, match="invalid_authored_stage_contract"):
        authored_stage_contract("workflow.review_score")
