"""LLM 客户端离线测试：response_format 不支持时的分层降级（L1→L3）。"""
from __future__ import annotations

from types import SimpleNamespace
import json

import pytest

from lib.llm_client import ChatClient, chat_json, load_backend, parse_json_robust


def _make_client(behavior: str) -> ChatClient:
    """构造离线 ChatClient：monkeypatch 掉底层 openai create，不发起网络。"""
    client = ChatClient(base_url="http://127.0.0.1:1/v1", api_key="sk-test", model="test-model")

    def fake_create(**kwargs):
        if behavior == "no_response_format" and "response_format" in kwargs:
            raise RuntimeError("400 BadRequest: response_format is unsupported/unavailable")
        if behavior == "always_reject_json" and "response_format" in kwargs:
            raise RuntimeError("400 BadRequest: response_format is unsupported/unavailable")
        msg = SimpleNamespace(content='{"ok": true}', reasoning_content=None)
        resp = SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)
        return resp

    client.client.chat.completions.create = fake_create
    return client


def test_fallback_when_response_format_unsupported():
    client = _make_client("no_response_format")
    out = chat_json(client, [{"role": "user", "content": "输出 JSON"}])
    assert out == {"ok": True}
    assert client.json_supported is False  # 已探测并记住
    # 后续调用直接走提示词路径，不再带 response_format
    out2 = chat_json(client, [{"role": "user", "content": "输出 JSON"}])
    assert out2 == {"ok": True}


def test_always_reject_json_still_parses_robust():
    # 即使 API 永远拒绝 response_format，降级路径 + 容错解析仍可工作
    client = _make_client("always_reject_json")
    out = chat_json(client, [{"role": "user", "content": "输出 JSON"}])
    assert out == {"ok": True}
    assert client.json_supported is False


def test_parse_json_robust_handles_fences_and_trailing():
    assert parse_json_robust('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_robust('{"a": 1} 尾随文本') == {"a": 1}
    assert parse_json_robust('前缀 {"a": [1, {"b": 2}]}') == {"a": [1, {"b": 2}]}


@pytest.mark.parametrize("wrapper", ["{}", "```json\n{}\n```", "解释：{} 完成"])
def test_json_documents_preserve_embedded_code_fences_and_even_backslash_escapes(wrapper):
    document = {"text": "步骤\n```python\nprint('hello')\n```\nC:\\folder\\",
                "note": "引号 \" 和 } 花括号", "metadata": {"source": "paper"}}
    response = json.dumps(document, ensure_ascii=False)
    assert parse_json_robust(wrapper.format(response)) == document


def test_incomplete_outer_document_cannot_be_replaced_with_complete_nested_metadata():
    with pytest.raises(ValueError, match="JSON 解析失败"):
        parse_json_robust('{"text":"unfinished", "metadata":{"source":"paper"}')


def test_chat_json_can_bound_output_tokens_for_team_workers():
    calls = []

    class FakeClient:
        def chat(self, messages, **kwargs):
            calls.append(kwargs)
            return '{"ok": true}'

    client = FakeClient()
    assert chat_json(client, [{"role": "user", "content": "JSON"}], max_tokens=2048) == {"ok": True}
    assert calls[0]["max_tokens"] == 2048
    for invalid in (True, 0, -1, "2048"):
        with pytest.raises(ValueError):
            chat_json(client, [], max_tokens=invalid)
    assert len(calls) == 1


def test_client_sets_no_proxy_before_httpx_construction(monkeypatch):
    """代理绕过必须在 OpenAI/httpx 构造前生效（httpx 构造时快照环境变量）。"""
    import os

    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    ChatClient(base_url="http://127.0.0.1:1/v1", api_key="sk-t", model="m")
    assert "127.0.0.1" in os.environ.get("NO_PROXY", "")
    assert "127.0.0.1" in os.environ.get("no_proxy", "")


def test_jev_role_uses_its_own_slot_and_environment_over_global_model(tmp_path, monkeypatch):
    import lib.llm_client as llm

    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "backends.yaml").write_text(
        "backends:\n"
        "  main: { base_url: 'https://main.invalid/v1', api_key_env: '', models: [default] }\n"
        "  reviewer: { base_url: 'http://127.0.0.1:9000/v1', api_key_env: '', models: [review-default] }\n"
        "default_backend: main\n"
        "default_model: default\n"
        "model_roles:\n"
        "  generation: { backend: main, model: generator }\n"
        "  jev: { backend: reviewer, model: reviewer-default }\n",
        encoding="utf-8")
    monkeypatch.setenv("LLM_MODEL", "global-generation-model")
    monkeypatch.setenv("JEV_BACKEND", "reviewer")
    monkeypatch.setenv("JEV_MODEL", "jev-environment-model")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(llm, "ChatClient", FakeClient)
    client, model = load_backend(tmp_path, role="jev")
    assert isinstance(client, FakeClient)
    assert model == "jev-environment-model"
    assert captured["base_url"] == "http://127.0.0.1:9000/v1"
