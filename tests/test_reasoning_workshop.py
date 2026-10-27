"""Effective author/reviewer requests and immutable prompt compatibility.

The provider transport is intercepted. These checks establish wiring and
failure routing, not the model's semantic acceptance rate.
"""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from lib.prompts import get, render
from lib.domain.workflow_node_prompts import snapshot_node_prompts
from lib.contexts.workflows.application.generate_cot import GenerateCot
from test_workflow_native_prefill import setup_workflow
from test_workflow_styled_generation import maintenance_reasoning_trace
from test_generation_node_use_cases import MemoryGenerationPort, REASONING, conversation, source


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


@pytest.mark.parametrize("prompt_id,version,expected", [
    ("workflow.sft_styled", "1.6.0", "e2b41109719607112ce4fb465f0c23dae5c0625f6a25dc122d864e4394694013"),
    ("workflow.sft_directed", "1.10.0", "58a588f3c110ad75a196ac939f473fe2c73055ad4fce9c8c9399ed5f03938151"),
    ("workflow.sft_answer", "1.7.0", "fa4784df80356a39f5d6a4c799afbd52c07402f0fb3b8b6a04d3fda456d5c9b8"),
    ("workflow.multiturn_assistant", "1.7.0", "36497373b0c9632a91d729e322bd6fae4887f481bdcbd142a92f3e2ef5b829f4"),
    ("workflow.cot_generate", "1.10.0", "ca83cb39f56f4282a7eb3087f612d5005de010db9e8bfbe4a4b3a6ae6be9d6d4"),
    ("workflow.cot_rationale", "1.2.0", "a2ec7599933bc8d4bbfba62496e89675a57ecd3875787f801bf98c275cefa33b"),
    ("workflow.cot_prefix_check", "1.3.0", "c5783192af9507e3b26396e446e3bd29ed01710cc9e966995168edeb9f87f9e9"),
    ("workflow.rationale_check", "1.8.0", "9331bb8105dfa2ff8a1293c00d3a9b3a8c047a21939348c6e6856a08733367b7"),
])
def test_historical_prompt_assets_remain_byte_identical(prompt_id, version, expected):
    assert sha256(get(prompt_id, version).template.encode()).hexdigest() == expected


def test_new_defaults_cover_both_cot_routes_but_literal_overrides_stay_owned_by_user():
    snapshot = snapshot_node_prompts(None, recipe_version=22)
    for node, prompt_id in [("sft", "workflow.sft_styled"), ("sft", "workflow.sft_directed"),
                            ("sft", "workflow.sft_answer"), ("multiturn", "workflow.multiturn_assistant"),
                            ("cot", "workflow.cot_generate"), ("cot", "workflow.cot_rationale")]:
        body = snapshot[node][prompt_id]
        assert body.count("有效推导示范 v2") == 1
        assert "实际草稿与支持桥梁" in body
        assert "不要求所有样本先计划、再反问、再总结" in body
    body = snapshot["cot"]["workflow.cot_rationale"]
    assert "本阶段只准备待核验的作者推理，不输出answer" in body
    custom = "按我的方法充分说明。{literal_placeholder}"
    overridden = snapshot_node_prompts({"cot": {"workflow.cot_rationale": custom}}, recipe_version=22)
    assert overridden["cot"]["workflow.cot_rationale"] == custom


@pytest.mark.parametrize("legacy", [False, True])
def test_native_sdk_and_reviewer_receive_frozen_guidance_only_on_new_runs(tmp_path, monkeypatch, legacy):
    workflow, sample, client, reviewer, sent, _ = setup_workflow(tmp_path, monkeypatch)
    try:
        assert workflow.recipe["native_prefix_review_style_version"] == 1
        if legacy:
            workflow.recipe.pop("native_prefix_review_style_version")
            for prompt_id, version in [("workflow.cot_rationale", "1.2.0"),
                                       ("workflow.cot_prefix_check", "1.3.0")]:
                workflow.recipe["node_prompt_templates"]["cot"][prompt_id] = render(get(prompt_id, version))
        style = workflow.generation_style("cot", sample["id"])
        style.update(reasoning_depth="deep", reasoning_depth_instruction="DEEP_SCOPE_SENTINEL")
        style["instruction"] += "\nDEEP_SCOPE_SENTINEL"
        original = deepcopy(style)
        result = workflow.styled_cot(sample, style)[0]
        assert result["status"] == "eligible"
        assert style == original
        assert len(sent) == 2  # No extra generator request introduced.
        author_system = sent[0]["messages"][0]["content"]
        assert "DEEP_SCOPE_SENTINEL" in author_system
        assert ("有效推导示范 v2" in author_system) is not legacy
        calls = [call for call in reviewer.calls
                 if "独立核验待作为原生API推理前缀" in call[0]["content"]]
        assert len(calls) == 1
        review_system = calls[0][0]["content"]
        assert ("DEEP_SCOPE_SENTINEL" in review_system) is not legacy
        assert ("有效推导核验 v2" in review_system) is not legacy
        payload = json.loads(calls[0][-1]["content"])
        assert ("generation_style" in payload) is not legacy
        if not legacy:
            assert payload["generation_style"] == original
        assert result["reasoning"] and result["answer"]
    finally:
        client.client.close()


@pytest.mark.parametrize("version", [None, 1, True])
def test_prefix_rejection_keeps_complete_reasoning_and_never_calls_answer(version):
    rationale = {"reasoning": REASONING, "reasoning_trace": maintenance_reasoning_trace()}
    port = MemoryGenerationPort([rationale])
    reviews = []
    def reject_prefix(key, prompt_id, payload, **options):
        reviews.append((deepcopy(payload), deepcopy(options)))
        return {"keep": False, "adherence": 2, "reason": "实际推导缺少必要支持关系。"}
    port.check_adherence = reject_prefix
    def unexpected_answer(*args, **kwargs):
        raise AssertionError("rejected prefix must not purchase an answer")
    port.request_native_answer = unexpected_answer
    style = {"instruction": "解释真实依赖并核验", "reasoning_depth": "deep"}
    generator = GenerateCot(port, {"native_prefix_review_style_version": version})
    result, feedback = generator.generate_native_answer(source(),
        {"prompt": conversation()["messages"][:-1], "source": source()}, style, None, 0)
    assert result is None and feedback == {"prefix_review": "实际推导缺少必要支持关系。"}
    payload, options = reviews[0]
    enabled = type(version) is int and version == 1
    assert ("generation_style" in payload) == enabled
    assert ("instruction" in options) == enabled
    assert payload["reasoning"] == REASONING
    assert payload["reasoning_trace"] == rationale["reasoning_trace"]
