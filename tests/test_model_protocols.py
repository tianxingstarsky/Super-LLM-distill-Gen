"""The three model APIs keep the same workflow contract without network calls."""
from types import SimpleNamespace as Obj

import pytest

from lib.llm_client import ChatClient, chat_json, load_backend
from lib.model_protocols import anthropic_request, responses_input


def test_responses_request_json_output_and_usage():
    client = ChatClient("https://api.example.test/v1", "test", "writer", api_format="responses")
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return Obj(output_text='{"answer":"ok"}', usage=Obj(input_tokens=17, output_tokens=8))

    client.client.responses.create = create
    messages = [{"role": "system", "content": "Return JSON"},
                {"role": "user", "content": [{"type": "text", "text": "Describe"},
                                              {"type": "image_url", "image_url": {"url": "data:image/png;base64,YQ=="}}]}]
    assert chat_json(client, messages, max_tokens=32768) == {"answer": "ok"}
    assert calls[0]["input"][1]["content"][1] == {"type": "input_image", "image_url": "data:image/png;base64,YQ=="}
    assert calls[0]["max_output_tokens"] == 32768
    assert calls[0]["text"] == {"format": {"type": "json_object"}}
    assert calls[0]["store"] is False
    assert client.usage == {"prompt_tokens": 17, "completion_tokens": 8, "calls": 1}


def test_responses_json_format_rejection_falls_back_without_leaking_error():
    client = ChatClient("https://api.example.test/v1", "test", "writer", api_format="responses")
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if "text" in kwargs:
            raise RuntimeError("400 BadRequest: text.format json_object unsupported SECRET")
        return Obj(output=[Obj(type="message", content=[Obj(type="output_text", text='{"ok":true}')])],
                   usage=Obj(input_tokens=1, output_tokens=2))

    client.client.responses.create = create
    assert chat_json(client, [{"role": "user", "content": "JSON"}], retries=1) == {"ok": True}
    assert len(calls) == 2 and "text" not in calls[1]
    assert client.json_supported is False


def test_responses_reasoning_model_retries_without_unsupported_temperature():
    client = ChatClient("https://api.example.test/v1", "test", "reasoner", api_format="responses")
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if "temperature" in kwargs:
            raise RuntimeError("400: temperature is not supported")
        return Obj(output_text="ready", usage=None)

    client.client.responses.create = create
    assert client.chat([{"role": "user", "content": "Proceed"}], max_tokens=32768) == "ready"
    assert len(calls) == 2 and "temperature" not in calls[1]


def test_chat_reasoning_model_uses_new_output_cap_parameter_when_required():
    client = ChatClient("https://api.example.test/v1", "test", "reasoner")
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if "max_tokens" in kwargs:
            raise RuntimeError("max_tokens is not supported; use max_completion_tokens")
        if "temperature" in kwargs:
            raise RuntimeError("temperature is not supported")
        return Obj(choices=[Obj(message=Obj(content="ready", reasoning_content=None))], usage=None)

    client.client.chat.completions.create = create
    assert client.chat([{"role": "user", "content": "Proceed"}], max_tokens=32768) == "ready"
    assert len(calls) == 3
    assert calls[2]["max_completion_tokens"] == 32768 and "temperature" not in calls[2]


def test_anthropic_request_system_image_output_limit_and_usage():
    client = ChatClient("https://api.example.test", "test", "claude", api_format="anthropic")
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return Obj(content=[Obj(type="thinking", text="internal"),
                            Obj(type="text", text='{"answer":"ok"}')],
                   usage=Obj(input_tokens=31, output_tokens=12))

    client.client.messages.create = create
    messages = [{"role": "system", "content": "You are careful"},
                {"role": "user", "content": [{"type": "text", "text": "Describe"},
                                              {"type": "image_url", "image_url": {"url": "data:image/png;base64,YQ=="}}]}]
    assert chat_json(client, messages, max_tokens=32768) == {"answer": "ok"}
    assert calls[0]["system"].startswith("Return only a valid JSON object.\n\nYou are careful")
    assert calls[0]["messages"][0]["content"][1]["source"] == {
        "type": "base64", "media_type": "image/png", "data": "YQ=="}
    assert calls[0]["max_tokens"] == 32768
    assert client.usage == {"prompt_tokens": 31, "completion_tokens": 12, "calls": 1}


def test_context_limits_and_unsupported_content_fail_before_provider_call():
    client = ChatClient("https://api.example.test/v1", "test", "writer",
                        api_format="responses", context_window_tokens=40000)
    client.client.responses.create = lambda **kwargs: pytest.fail("request should not be sent")
    with pytest.raises(ValueError, match="model_context_window_exceeded"):
        client.chat([{"role": "user", "content": "x" * 8000}], max_tokens=32768)
    with pytest.raises(ValueError, match="max_output_tokens_exceeds_context_window"):
        client.chat([{"role": "user", "content": "x"}], max_tokens=40000)
    with pytest.raises(ValueError, match="unsupported_model_message_content"):
        responses_input([{"role": "user", "content": [{"type": "audio", "data": "secret"}]}])
    with pytest.raises(ValueError, match="unsupported_anthropic_image_url"):
        anthropic_request([{"role": "user", "content": [{"type": "image_url", "image_url": "https://example.test/a.png"}]}],
                          json_mode=False)


def test_backend_format_is_loaded_and_invalid_format_rejected(tmp_path, monkeypatch):
    config = tmp_path / "configs"
    config.mkdir()
    (config / "backends.yaml").write_text(
        "backends:\n  claude: {base_url: 'https://api.example.test', api_key_env: 'TEST_KEY', "
        "api_format: anthropic, models: [claude]}\ndefault_backend: claude\n", encoding="utf-8")
    monkeypatch.setenv("TEST_KEY", "test")
    client, _ = load_backend(tmp_path, context_window_tokens=131072)
    assert client.api_format == "anthropic" and client.context_window_tokens == 131072
    (config / "backends.yaml").write_text(
        "backends:\n  claude: {base_url: 'https://api.example.test', api_format: typo, models: [claude]}\n"
        "default_backend: claude\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid_api_format"):
        load_backend(tmp_path)


def test_anthropic_backend_without_explicit_env_uses_anthropic_key_only(tmp_path, monkeypatch):
    config = tmp_path / "configs"
    config.mkdir()
    (config / "backends.yaml").write_text(
        "backends:\n  claude: {base_url: 'https://api.example.test', api_format: anthropic, models: [claude]}\n"
        "default_backend: claude\n", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "wrong-secret")
    client, _ = load_backend(tmp_path)
    assert client.client.api_key == "anthropic-secret"
