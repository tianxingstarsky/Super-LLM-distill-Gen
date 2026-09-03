"""LLM 客户端离线测试：response_format 不支持时的分层降级（L1→L3）。"""
from __future__ import annotations

from types import SimpleNamespace

from lib.llm_client import ChatClient, chat_json, parse_json_robust


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
