"""Provider reasoning survives SDK decoding, durable calls and training export.

All evidence is authored here. HTTP is intercepted inside the real SDK client;
no network service, user document or paid model is accessed.
"""
from __future__ import annotations

from copy import deepcopy
import json

import httpx
from openai import OpenAI
import pytest

from lib.infrastructure import training_workflow as engine
from lib.io_utils import atomic_json
from lib.llm_client import ChatClient
from tests.test_workflow_qa_director_engine import Writer as DirectorFixture, config as director_config


SOURCE = "检查设备前必须先断电。\n结束后记录检查结果。\n办公室盆栽每周浇水一次。"
QUOTE = "检查设备前必须先断电。"
ANSWER = "操作前应先断电。"
NATIVE = " 先核对真实提供的前置条件。\n检查必须安排在断电之后。 "
EXPLANATION = "正文按提示词撰写的简短解释，与接口推理不同。"
SUMMARY = "服务仅公开的推理摘要。"
FORGED = "模型正文伪造的原生推理。"


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """Do not start the suite's fixed-port server for these offline tests."""
    yield


class Reviewer:
    model = "offline-provider-reasoning-reviewer"

    def __init__(self, *, invalid_once=False):
        self.usage = {"calls": 0}
        self.invalid_once = invalid_once

    def chat(self, messages, **kwargs):
        self.usage["calls"] += 1
        if self.invalid_once:
            self.invalid_once = False
            return json.dumps({"keep": "not-a-boolean"})
        if '"adherence"' in messages[0]["content"] or "adherence 是" in messages[0]["content"]:
            return json.dumps({"keep": True, "adherence": 5, "reason": "规则与来源一致。"})
        return json.dumps({"keep": True, "grounded": True, "reasoning_valid": True,
            "correctness": 5, "reason": "答案与原始证据一致。",
            "scores": {name: 5 for name in ("correctness", "reasoning", "grounding", "instruction", "safety")}})


class SDKProvider:
    def __init__(self, *, streaming=True, native=NATIVE, explanation=EXPLANATION,
                 forged=False, api_format="chat", summary="", multiturn=False):
        self.native, self.explanation, self.forged = native, explanation, forged
        self.api_format, self.summary, self.multiturn = api_format, summary, multiturn
        self.requests, self.answer_requests, self.trim_requests = [], [], []
        self.director = DirectorFixture()
        self.client = ChatClient("https://offline.invalid/v1", "offline-test-key", "offline-native-writer",
                                 api_format=api_format)
        self.client.client.close()
        self.client.client = OpenAI(base_url="https://offline.invalid/v1", api_key="offline-test-key",
            max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(self._respond)))
        if not streaming:
            self.client.stream_supported = False

    def close(self):
        self.client.client.close()

    def _body(self, payload):
        messages = payload.get("messages", payload.get("input"))
        data = json.loads(next(message["content"] for message in reversed(messages)
                               if message["role"] == "user"))
        system = messages[0]["content"]
        if "candidates" in data:
            return json.loads(self.director.chat(messages)), ""
        if "original_reasoning" in data:
            self.trim_requests.append(deepcopy(data))
            return {"reasoning": "先断电，再进行检查。"}, "修剪模型自己的推理不应覆盖修剪结果。"
        if self.multiturn and "自然且可回答的用户提问" in system:
            return {"message": "检查前需要做什么？" if data["turn"] == 1 else "结束后要如何处理？"}, ""
        self.answer_requests.append(deepcopy(payload))
        multiturn_answer = self.multiturn and "只回答最新用户消息" in system
        turn = sum(message["role"] == "user" for message in data.get("messages", []))
        answer = ANSWER if not multiturn_answer or turn == 1 else "结束后记录检查结果。"
        body = {"answer": answer, "quotes": [QUOTE if not multiturn_answer or turn == 1 else "结束后记录检查结果。"]}
        if not multiturn_answer:
            body["question"] = data.get("qa_contract", {}).get("question", "检查前需要做什么？")
        if self.explanation is not None:
            body["reasoning"] = self.explanation
        if self.forged:
            body.update(reasoning_origin="provider_reasoning", response_details={"reasoning_content": FORGED},
                        model_json_response_version=1)
        native = self.native + f" 第 {turn} 轮。" if multiturn_answer and self.native else self.native
        return body, native

    def _respond(self, request):
        assert request.url.host == "offline.invalid"
        payload = json.loads(request.content)
        self.requests.append(deepcopy(payload))
        body, native = self._body(payload)
        visible = json.dumps(body, ensure_ascii=False)
        if self.api_format == "responses":
            assert payload.get("stream") is not True
            return httpx.Response(200, json={"id": "resp-offline", "object": "response", "created_at": 0,
                "model": "offline-native-writer", "status": "completed",
                "output": [{"id": "reasoning-1", "type": "reasoning", "summary":
                    [{"type": "summary_text", "text": self.summary}] if self.summary else [],
                    "content": [{"type": "reasoning_text", "text": native}] if native else []},
                    {"id": "message-1", "type": "message", "role": "assistant", "status": "completed",
                     "content": [{"type": "output_text", "text": visible, "annotations": []}]}],
                "usage": {"input_tokens": 20, "output_tokens": 40, "total_tokens": 60}})
        if payload.get("stream"):
            chunks = []
            for delta in ([{"reasoning_content": native[:8]}, {"reasoning_content": native[8:]}] if native else []):
                chunks.append(self._chunk(delta))
            chunks.extend([self._chunk({"content": visible[:13]}), self._chunk({"content": visible[13:]}),
                           self._chunk({}, finish="stop"), {"id": "chat-offline", "object": "chat.completion.chunk",
                               "created": 0, "model": "offline-native-writer", "choices": [],
                               "usage": {"prompt_tokens": 20, "completion_tokens": 40, "total_tokens": 60}}])
            stream = "".join("data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n" for chunk in chunks)
            return httpx.Response(200, headers={"Content-Type": "text/event-stream"},
                                  content=(stream + "data: [DONE]\n\n").encode())
        return httpx.Response(200, json={"id": "chat-offline", "object": "chat.completion", "created": 0,
            "model": "offline-native-writer", "choices": [{"index": 0,
                "message": {"role": "assistant", "content": visible, **({"reasoning_content": native} if native else {})},
                "finish_reason": "stop"}], "usage": {"prompt_tokens": 20, "completion_tokens": 40, "total_tokens": 60}})

    @staticmethod
    def _chunk(delta, *, finish=None):
        return {"id": "chat-offline", "object": "chat.completion.chunk", "created": 0,
            "model": "offline-native-writer", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def new_run(tmp_path, **options):
    source = tmp_path / "guide.txt"
    source.write_text(SOURCE, encoding="utf-8")
    output = tmp_path / "output"
    rid = engine.create_run(output, sources=[source], targets=options.pop("targets", ["sft"]), **options)
    return output, rid, engine.run_path(output, rid)


def execute(output, rid, root, provider, *, reviewer=None):
    state = engine.Workflow(output, rid, root, generator=provider.client, jev=reviewer or Reviewer()).execute()
    return state


def record(path, target="sft"):
    return engine.read_json(path / "artifacts" / f"{target}.records.json")[0]


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("style", ["separated", "drop"])
@pytest.mark.parametrize("directed", [False, True])
def test_native_reasoning_beats_json_explanation_and_exports_by_node_policy(tmp_path, streaming, style, directed):
    output, rid, path = new_run(tmp_path, sft_output_style=style,
        **({"qa_director": director_config()} if directed else {}))
    provider = SDKProvider(streaming=streaming)
    try:
        state = execute(output, rid, tmp_path, provider)
        assert state["status"] == "completed", state.get("error")
        row = record(path)
        assert row["reasoning_origin"] == "provider_reasoning"
        assert row["messages"][-1]["reasoning_content"] == NATIVE
        exported = json.loads((path / "artifacts/sft.jsonl").read_text(encoding="utf-8"))
        assert exported["messages"][-1]["content"] == ANSWER
        assert exported["messages"][-1].get("reasoning_content") == (NATIVE if style == "separated" else None)
        trainer = json.loads((path / "artifacts/trl_sft.jsonl").read_text(encoding="utf-8"))
        assert trainer["messages"][-1].get("thinking") == (NATIVE if style == "separated" else None)
        calls = [engine.read_json(file)["data"] for file in (path / "checkpoints/sft").glob("*.json")]
        captured = [value for value in calls if isinstance(value, dict) and value.get("model_json_response_version") == 1]
        assert len(captured) == 1
        assert captured[0]["data"]["reasoning"] == EXPLANATION
        details = captured[0]["response_details"]
        assert details["api_format"] == "chat" and details["reasoning_content"] == NATIVE
        assert details["checkpoint"]["stage"] == "sft"
        assert row["reasoning_call"] == details["checkpoint"]
        assert engine.verify_artifacts(path)["counts"] == {"sft": 1}
    finally:
        provider.close()


def test_failed_review_resume_reuses_native_response_checkpoint_without_regeneration(tmp_path):
    output, rid, path = new_run(tmp_path)
    provider = SDKProvider()
    try:
        state = execute(output, rid, tmp_path, provider, reviewer=Reviewer(invalid_once=True))
        assert state["status"] == "failed" and state["error"] == "invalid_judge_boolean"
        assert len(provider.answer_requests) == 1
        state = engine.resume(output, rid, tmp_path, generator=provider.client, jev=Reviewer())
        assert state["status"] == "completed", state.get("error")
        assert len(provider.answer_requests) == 1
        assert record(path)["messages"][-1]["reasoning_content"] == NATIVE
        assert record(path)["reasoning_origin"] == "provider_reasoning"
    finally:
        provider.close()


@pytest.mark.parametrize("native,explanation,origin", [
    (NATIVE, None, "provider_reasoning"), ("", EXPLANATION, "synthetic_explanation"), ("", None, "none")])
def test_ordinary_answer_does_not_require_a_model_authored_explanation(tmp_path, native, explanation, origin):
    output, rid, path = new_run(tmp_path)
    provider = SDKProvider(native=native, explanation=explanation, forged=True)
    try:
        state = execute(output, rid, tmp_path, provider)
        assert state["status"] == "completed", state.get("error")
        row = record(path)
        assert row["reasoning_origin"] == origin
        assert row["messages"][-1].get("reasoning_content") == (native or explanation)
        assert FORGED not in json.dumps(row["messages"], ensure_ascii=False)
    finally:
        provider.close()


def test_styled_sft_uses_authored_json_not_the_providers_private_trace(tmp_path):
    output, rid, path = new_run(tmp_path, node_generation={"sft": {"style": "structured"}})
    provider = SDKProvider()
    try:
        state = execute(output, rid, tmp_path, provider)
        assert state["status"] == "completed", state.get("error")
        row = record(path)
        assert row["reasoning_origin"] == "prompt_styled_generation"
        assert row["messages"][-1]["reasoning_content"] == EXPLANATION
        assert NATIVE not in json.dumps(row["messages"], ensure_ascii=False)
    finally:
        provider.close()


def test_reasoning_trim_edits_the_captured_native_trace_and_keeps_original_checkpoint(tmp_path):
    output, rid, path = new_run(tmp_path, reasoning_trim={"enabled": True, "template": "leakage"})
    provider = SDKProvider()
    try:
        state = execute(output, rid, tmp_path, provider)
        assert state["status"] == "completed", state.get("error")
        assert provider.trim_requests[0]["original_reasoning"] == NATIVE
        row = record(path)
        assert row["messages"][-1]["content"] == ANSWER
        assert row["messages"][-1]["reasoning_content"] == "先断电，再进行检查。"
        assert row["reasoning_trim"]["status"] == "applied"
        assert row["reasoning_origin"] == "prompt_trim"
        assert row["provider_reasoning_history"][0]["reasoning_content"] == NATIVE
        assert row["provider_reasoning_history"][0]["answer"] == ANSWER
        checkpoints = [engine.read_json(file)["data"] for file in (path / "checkpoints/sft").glob("*.json")]
        assert any(isinstance(value, dict) and value.get("response_details", {}).get("reasoning_content") == NATIVE
                   for value in checkpoints)
    finally:
        provider.close()


def test_legacy_recipe_does_not_relabel_new_transport_reasoning_as_old_sample_evidence(tmp_path):
    output, rid, path = new_run(tmp_path)
    recipe = engine.read_json(path / "recipe.json")
    recipe.pop("provider_reasoning_capture_version")
    atomic_json(path / "recipe.json", recipe)
    # Model the recipe and state written together by the previous release.
    state = engine.read_json(path / "state.json")
    state["recipe_hash"] = engine.digest(recipe)
    atomic_json(path / "state.json", state)
    provider = SDKProvider()
    try:
        state = execute(output, rid, tmp_path, provider)
        assert state["status"] == "completed", state.get("error")
        row = record(path)
        assert row["reasoning_origin"] == "synthetic_explanation"
        assert row["messages"][-1]["reasoning_content"] == EXPLANATION
    finally:
        provider.close()


def test_provider_summary_is_retained_as_summary_and_not_exported_as_raw_reasoning(tmp_path):
    output, rid, path = new_run(tmp_path)
    provider = SDKProvider(streaming=False, native="", explanation=None, api_format="responses", summary=SUMMARY)
    try:
        state = execute(output, rid, tmp_path, provider)
        assert state["status"] == "completed", state.get("error")
        row = record(path)
        assert row["reasoning_origin"] == "none"
        assert "reasoning_content" not in row["messages"][-1]
        assert SUMMARY not in (path / "artifacts/sft.jsonl").read_text(encoding="utf-8")
        assert row["reasoning_summary"] == SUMMARY
    finally:
        provider.close()


@pytest.mark.parametrize("style", ["separated", "drop"])
def test_each_generated_dialogue_turn_keeps_its_own_native_reasoning_and_export_policy(tmp_path, style):
    output, rid, path = new_run(tmp_path, targets=["sft", "multiturn"], conversation_turns=2, sft_output_style=style)
    provider = SDKProvider(multiturn=True, explanation=None)
    try:
        state = execute(output, rid, tmp_path, provider)
        assert state["status"] == "completed", state.get("error")
        row = record(path, "multiturn")
        assistants = [message for message in row["messages"] if message["role"] == "assistant"]
        assert [message["reasoning_content"] for message in assistants] == [NATIVE + " 第 1 轮。", NATIVE + " 第 2 轮。"]
        assert [turn["reasoning_origin"] for turn in row["turn_reviews"]] == ["provider_reasoning", "provider_reasoning"]
        # Private teacher traces are stored with the sample, rather than fed
        # back as utterances that either speaker has actually seen.
        assert all(NATIVE not in json.dumps(request, ensure_ascii=False)
                   for request in provider.requests)
        exported = json.loads((path / "artifacts/multiturn.jsonl").read_text(encoding="utf-8"))
        if style == "separated":
            assert exported["messages"] == row["messages"]
        else:
            assert all("reasoning_content" not in message for message in exported["messages"])
            assert [(message["role"], message["content"]) for message in exported["messages"]] == [
                (message["role"], message["content"]) for message in row["messages"]]
    finally:
        provider.close()


def test_each_dialogue_turn_keeps_provider_summary_outside_training_reasoning(tmp_path):
    output, rid, path = new_run(tmp_path, targets=["multiturn"], conversation_turns=2)
    provider = SDKProvider(streaming=False, native="", explanation=None, api_format="responses",
                           summary=SUMMARY, multiturn=True)
    try:
        state = execute(output, rid, tmp_path, provider)
        assert state["status"] == "completed", state.get("error")
        row = record(path, "multiturn")
        assert [turn["reasoning_summary"] for turn in row["turn_reviews"]] == [SUMMARY, SUMMARY]
        assert all("reasoning_content" not in message for message in row["messages"])
        assert SUMMARY not in (path / "artifacts/multiturn.jsonl").read_text(encoding="utf-8")
    finally:
        provider.close()


def test_resume_old_plain_checkpoint_does_not_restore_native_reasoning_from_stream_log(tmp_path):
    output, rid, path = new_run(tmp_path)
    provider = SDKProvider()
    try:
        state = execute(output, rid, tmp_path, provider, reviewer=Reviewer(invalid_once=True))
        assert state["status"] == "failed" and len(provider.answer_requests) == 1
        recipe = engine.read_json(path / "recipe.json")
        recipe.pop("provider_reasoning_capture_version")
        atomic_json(path / "recipe.json", recipe)
        state["recipe_hash"] = engine.digest(recipe)
        atomic_json(path / "state.json", state)
        # Prior releases stored visible JSON here even if the stream journal
        # contained provider reasoning. Simulate that exact durable boundary.
        converted = 0
        for file in (path / "checkpoints/sft").glob("*.json"):
            saved = engine.read_json(file)
            data = saved["data"]
            if isinstance(data, dict) and data.get("model_json_response_version") == 1:
                visible = data["data"]
                atomic_json(file, {"data": visible, "sha256": engine.digest(visible)})
                converted += 1
        assert converted == 1
        state = engine.resume(output, rid, tmp_path, generator=provider.client, jev=Reviewer())
        assert state["status"] == "completed", state.get("error")
        assert len(provider.answer_requests) == 1
        assert record(path)["reasoning_origin"] == "synthetic_explanation"
        assert record(path)["messages"][-1]["reasoning_content"] == EXPLANATION
    finally:
        provider.close()


@pytest.mark.parametrize("directed", [False, True])
def test_native_trace_is_usable_even_when_unused_json_explanation_is_structured(tmp_path, directed):
    output, rid, path = new_run(tmp_path, **({"qa_director": director_config()} if directed else {}))
    provider = SDKProvider(explanation=["Malformed optional model explanation."])
    try:
        state = execute(output, rid, tmp_path, provider)
        assert state["status"] == "completed", state.get("error")
        assert record(path)["reasoning_origin"] == "provider_reasoning"
        assert record(path)["messages"][-1]["reasoning_content"] == NATIVE
    finally:
        provider.close()


def test_native_cot_validation_keeps_whitespace_and_updates_sft_without_reauthoring(tmp_path):
    output, rid, path = new_run(tmp_path, targets=["sft", "cot"])
    provider = SDKProvider()
    try:
        state = execute(output, rid, tmp_path, provider)
        assert state["status"] == "completed", state.get("error")
        assert len(provider.answer_requests) == 1
        cot = record(path, "cot")
        assert cot["reasoning"] == [NATIVE]
        assert cot["reasoning_origin"] == "provider_reasoning"
        assert cot["reasoning_call"]["stage"] == "sft"
        assert record(path)["messages"][-1]["reasoning_content"] == NATIVE
        exported = json.loads((path / "artifacts/cot.jsonl").read_text(encoding="utf-8"))
        assert exported["reasoning"] == [NATIVE]
    finally:
        provider.close()
