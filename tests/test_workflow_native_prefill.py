"""Real SDK transport through durable workflow checkpoints; no paid requests."""
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace as Obj

import pytest

from lib.domain.workflow_generation import validate_node_generation
from lib.infrastructure import training_workflow as engine
from lib.llm_client import ChatClient
from test_workflow_styled_generation import run, Reviewer, maintenance_reasoning_trace, TEXT, ANSWER, SOURCE_REASONING


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


PREFIX = "维护必须先断电，线路检查依赖这个安全前提；检查完成后再记录结果。"


def setup_workflow(tmp_path, monkeypatch, *, legacy_json=False):
    output, rid, path = run(tmp_path, conversation=True, targets=["cot"],
        node_generation={"cot": {"style": "default", "answer_mode": "native_prefill"}},
        **({"node_prompts": {"cot": {"workflow.cot_native_answer": '仅返回JSON：{"answer":"最终回答"}。'}}}
           if legacy_json else {}))
    client = ChatClient("https://api.deepseek.com/v1", "offline-key", "deepseek-flash",
        reasoning={"mode": "enabled", "effort": "high"})
    client.stream_supported = False
    sent = []
    failures = [False]
    def create(**kwargs):
        sent.append(deepcopy(kwargs))
        last = kwargs["messages"][-1]
        if last.get("prefix"):
            assert last == {"role": "assistant", "content": "", "prefix": True, "reasoning_content": PREFIX}
            if failures[0]:
                failures[0] = False
                raise ValueError("model_reasoning_prefill_rejected")
            body, native = {"answer": ANSWER}, "provider-only continuation"
        else:
            assert last == {"role": "user", "content": "设备应如何维护？"}
            body, native = {"reasoning": PREFIX, "reasoning_trace": maintenance_reasoning_trace()}, "rationale-model native"
        content = ANSWER if last.get("prefix") and not legacy_json else json.dumps(body, ensure_ascii=False)
        return Obj(choices=[Obj(message=Obj(content=content, reasoning_content=native),
                                 finish_reason="stop")], usage=Obj(prompt_tokens=12, completion_tokens=8))
    client.client.chat.completions.create = create
    monkeypatch.setattr(client.client, "with_options", lambda **kwargs: Obj(chat=Obj(completions=Obj(create=create))))
    reviewer = Reviewer()
    original = reviewer.chat
    def review(messages, **kwargs):
        if "此阶段不生成" in messages[0]["content"]:
            raise AssertionError("wrong review prompt")
        if "把作者推理作为原生通道前缀" in messages[0]["content"]:
            reviewer.calls.append(deepcopy(messages))
            reviewer.usage["calls"] += 1
            return json.dumps({"keep": True, "adherence": 5, "reason": "断电前提充分。"})
        return original(messages, **kwargs)
    reviewer.chat = review
    workflow = engine.Workflow(output, rid, tmp_path, generator=client, jev=reviewer)
    workflow.stage = "cot"
    sample = {"id": "original-case", "source_id": "s1", "status": "eligible",
        "evidence_level": "document_quote", "quotes": [TEXT], "source_context": {"kind": "document", "text": TEXT},
        "messages": [{"role": "user", "content": "设备应如何维护？"},
                     {"role": "assistant", "content": ANSWER, "reasoning_content": SOURCE_REASONING}]}
    return workflow, sample, client, reviewer, sent, failures


def test_native_workflow_prefix_is_exact_and_not_dialogue_text(tmp_path, monkeypatch):
    workflow, sample, client, reviewer, sent, _ = setup_workflow(tmp_path, monkeypatch)
    assert workflow.recipe["version"] == 21
    result = workflow.styled_cot(sample, workflow.generation_style("cot", sample["id"]))[0]
    assert result["status"] == "eligible"
    assert result["reasoning"] == [PREFIX] and result["answer"] == ANSWER
    assert result["reasoning_origin"] == "authored_prefix_native_answer"
    assert result["answer_response_details"]["reasoning_content"] == "provider-only continuation"
    assert result["reasoning_prefill"]["prefix_sha256"] == hashlib.sha256(PREFIX.encode()).hexdigest()
    assert len(sent) == 2
    assert SOURCE_REASONING not in json.dumps(sent, ensure_ascii=False)
    assert PREFIX not in json.dumps(sent[-1]["messages"][:-1], ensure_ascii=False)
    assert not any(m.get("role") == "tool" or "tool_calls" in m for m in sent[-1]["messages"])
    assert any(m.get("role") == "tool" for m in sent[0]["messages"])
    assert result["question"] == sample["messages"][:-1]
    assert client.base_url == "https://api.deepseek.com/v1" and client.model == "deepseek-flash"
    client.client.close()


def test_failed_answer_reuses_verified_prefix_and_exact_roles(tmp_path, monkeypatch):
    workflow, sample, client, reviewer, sent, failures = setup_workflow(tmp_path, monkeypatch)
    failures[0] = True
    with pytest.raises(Exception, match="model_reasoning_prefill_rejected"):
        workflow.styled_cot(sample, workflow.generation_style("cot", sample["id"]))
    second = workflow.styled_cot(sample, workflow.generation_style("cot", sample["id"]))[0]
    assert second["status"] == "eligible"
    assert len(sent) == 3
    assert sent[1]["messages"] == sent[2]["messages"]
    assert sum("把作者推理作为原生通道前缀" in request[0]["content"] for request in reviewer.calls) == 1
    client.client.close()


def test_old_generation_config_stays_identical_and_inactive_prefill_is_rejected():
    old = {"cot": {"enabled": True, "style": "default", "instruction": ""}}
    assert validate_node_generation(old) == old
    for value in ({"sft": {"answer_mode": "native_prefill"}},
                  {"cot": {"enabled": False, "answer_mode": "native_prefill"}}):
        with pytest.raises(ValueError, match="invalid_cot_answer_mode"):
            validate_node_generation(value)


def test_saved_beta_json_contract_decodes_only_its_real_answer_field(tmp_path, monkeypatch):
    workflow, sample, client, reviewer, sent, _ = setup_workflow(tmp_path, monkeypatch, legacy_json=True)
    result = workflow.styled_cot(sample, workflow.generation_style("cot", sample["id"]))[0]
    assert result["status"] == "eligible" and result["answer"] == ANSWER
    raw = result["answer_response_details"]["visible_response_envelope"]
    assert json.loads(raw) == {"answer": ANSWER}
    assert raw != result["answer"]
    client.client.close()
