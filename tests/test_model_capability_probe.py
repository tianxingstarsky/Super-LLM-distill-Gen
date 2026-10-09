"""Offline tests: simulated SDKs must prove fixture content and tool structure."""
import base64
from io import BytesIO
import json
import re
from types import SimpleNamespace

import pytest
from PIL import Image
from pypdf import PdfReader

from lib.infrastructure import model_capability_probe as probe


class ProviderFailure(Exception):
    def __init__(self, status, message, code=None):
        super().__init__(message)
        self.status_code = status
        self.body = {"error": {"message": message, "code": code}}


class FakeClient:
    def __init__(self, api_format, behavior=None):
        self.api_format = api_format
        self.behavior = behavior or {}
        self.requests = []
        self.closed = False
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        self.responses = SimpleNamespace(create=self.create)
        self.messages = SimpleNamespace(create=self.create)

    def create(self, **kwargs):
        self.requests.append(kwargs)
        parts = kwargs.get("messages", kwargs.get("input"))[0]["content"]
        prompt = parts[-1]["text"]
        if "tools" in kwargs:
            feature = "tools"
            expected = re.search(r"[a-f0-9]{16}", prompt)[0]
        elif len(parts) == 1:
            feature = "text"
            expected = re.search(r"[a-f0-9]{16}", prompt)[0]
        elif parts[0]["type"] in {"input_file", "file", "document"}:
            feature = "pdf"
            file = parts[0]
            raw = file.get("file_data", file.get("file", {}).get("file_data"))
            encoded = file["source"]["data"] if raw is None else raw.split(",", 1)[1]
            expected = PdfReader(BytesIO(base64.b64decode(encoded)), strict=True).pages[0].extract_text().strip()
            assert expected not in prompt
            assert expected not in json.dumps({key: value for key, value in file.items() if key in {"filename", "title", "context"}})
        else:
            feature = "vision"
            image = parts[0]
            if self.api_format == "anthropic":
                encoded = image["source"]["data"]
            else:
                url = image["image_url"]
                encoded = (url["url"] if isinstance(url, dict) else url).split(",", 1)[1]
            with Image.open(BytesIO(base64.b64decode(encoded))) as parsed:
                expected = dict((rgb, color) for color, rgb in probe._COLORS)[parsed.getpixel((40, 40))]
            assert expected not in prompt.lower()
        action = self.behavior.get(feature)
        if isinstance(action, Exception):
            raise action
        response = self.response(expected, feature)
        return action(response, expected) if callable(action) else response

    def response(self, expected, feature):
        if self.api_format == "chat":
            message = {"content": expected}
            if feature == "tools":
                message["tool_calls"] = [{"id": "test-call", "type": "function", "function": {
                    "name": "confirm_probe", "arguments": json.dumps({"nonce": expected})}}]
            return {"choices": [{"message": message, "finish_reason": "tool_calls" if feature == "tools" else "stop"}],
                    "usage": {"prompt_tokens": 17, "completion_tokens": 9}}
        if self.api_format == "responses":
            output = [{"type": "function_call", "name": "confirm_probe", "arguments": json.dumps({"nonce": expected})}] if feature == "tools" else []
            return {"output_text": expected, "output": output, "status": "completed",
                    "usage": {"input_tokens": 17, "output_tokens": 9}}
        content = [{"type": "tool_use", "id": "test-call", "name": "confirm_probe", "input": {"nonce": expected}}] if feature == "tools" else [{"type": "text", "text": expected}]
        return {"content": content, "usage": {"input_tokens": 17, "output_tokens": 9}}

    def close(self):
        self.closed = True


def install_sdk(monkeypatch, api_format="chat", behavior=None):
    client = FakeClient(api_format, behavior)
    options = []

    def factory(**kwargs):
        options.append(kwargs)
        return client

    if api_format == "anthropic":
        import anthropic
        monkeypatch.setattr(anthropic, "Anthropic", factory)
    else:
        import openai
        monkeypatch.setattr(openai, "OpenAI", factory)
    return client, options


def backend(api_format="chat"):
    return {"api_format": api_format, "base_url": "https://fixture.invalid/v1", "api_key": "sk-fixture",
            "max_tokens": 32768, "context_window": 131072}


@pytest.mark.parametrize("api_format", probe.API_FORMATS)
def test_all_features_require_content_and_real_tool_call(monkeypatch, api_format):
    client, options = install_sdk(monkeypatch, api_format)
    result = probe.probe_model(backend(api_format), "test-model")
    assert all(test["status"] == "passed" for test in result["tests"].values())
    assert result["tests"]["vision"]["evidence"] == "image_content_verified"
    assert result["tests"]["pdf"]["evidence"] == "pdf_content_verified"
    assert result["tests"]["tools"]["evidence"] == "function_call_verified"
    assert len(client.requests) == 4 and client.closed
    assert result["usage"] == {"input_tokens": 68, "output_tokens": 36, "calls": 4, "reported_calls": 4}
    assert options[0]["max_retries"] == 0 and options[0]["timeout"] == 12
    assert all(request["timeout"] == 12 and request["stream"] is False for request in client.requests)
    assert all(request.get("max_tokens", request.get("max_output_tokens")) == 256 for request in client.requests)
    assert result["api_format"] == api_format and result["model"] == "test-model"
    assert result["tested_at"].endswith("+00:00") and result["elapsed_ms"] >= 0


@pytest.mark.parametrize("api_format", probe.API_FORMATS)
def test_pdf_uses_native_attachment_without_upload_or_nonce_prompt(monkeypatch, api_format):
    client, _ = install_sdk(monkeypatch, api_format)
    result = probe.probe_model(backend(api_format), "model", ["pdf"])
    assert result["tests"]["pdf"]["status"] == "passed"
    request = client.requests[1]
    part = request.get("messages", request.get("input"))[0]["content"][0]
    if api_format == "chat":
        assert part["type"] == "file" and part["file"]["file_data"].startswith("data:application/pdf;base64,")
    elif api_format == "responses":
        assert part["type"] == "input_file" and part["file_data"].startswith("data:application/pdf;base64,")
        assert request["store"] is False
    else:
        assert part["type"] == "document" and part["source"]["media_type"] == "application/pdf"
    assert result["tests"]["vision"]["status"] == "skipped"
    assert result["usage"]["calls"] == 2


def test_pdf_fixture_is_valid_extractable_and_renderable():
    import pypdfium2

    nonce = "8a04bed983762ad1"
    data = probe._pdf_fixture(nonce)
    assert len(data) < 1500
    pdf = PdfReader(BytesIO(data), strict=True)
    assert len(pdf.pages) == 1 and pdf.pages[0].extract_text().strip() == nonce
    with pypdfium2.PdfDocument(data) as rendered:
        page = rendered[0]
        bitmap = page.render(scale=1)
        image = bitmap.to_pil()
        assert image.size == (256, 128) and image.convert("L").getextrema()[0] < 100
        image.close()
        bitmap.close()
        page.close()


@pytest.mark.parametrize("api_format", probe.API_FORMATS)
def test_http_success_with_wrong_image_or_pdf_content_fails(monkeypatch, api_format):
    def wrong(response, expected):
        return FakeClient(api_format).response("wrong-content", "text")

    client, _ = install_sdk(monkeypatch, api_format, {"vision": wrong, "pdf": wrong})
    result = probe.probe_model(backend(api_format), "model")
    assert result["tests"]["vision"]["status"] == result["tests"]["pdf"]["status"] == "failed"
    assert result["tests"]["vision"]["reason_code"] == "probe_reply_mismatch"
    assert result["tests"]["tools"]["status"] == "passed"
    assert client.closed


@pytest.mark.parametrize("api_format", probe.API_FORMATS)
def test_tools_accepted_but_ignored_does_not_pass(monkeypatch, api_format):
    install_sdk(monkeypatch, api_format, {"tools": lambda response, nonce: FakeClient(api_format).response(nonce, "text")})
    result = probe.probe_model(backend(api_format), "model", ["tools"])
    assert result["tests"]["tools"]["status"] == "failed"
    assert result["tests"]["tools"]["reason_code"] == "probe_reply_mismatch"


@pytest.mark.parametrize("api_format", probe.API_FORMATS)
@pytest.mark.parametrize("problem", ["wrong_name", "wrong_nonce", "extra_argument", "multiple_calls", "invalid_json"])
def test_invalid_tool_name_or_arguments_are_rejected(api_format, problem):
    response = FakeClient(api_format).response("0123456789abcdef", "tools")
    if api_format == "chat":
        calls = response["choices"][0]["message"]["tool_calls"]
        call = calls[0]["function"]
    elif api_format == "responses":
        calls = response["output"]
        call = calls[0]
    else:
        calls = response["content"]
        call = calls[0]
    key = "input" if api_format == "anthropic" else "arguments"
    if problem == "wrong_name":
        call["name"] = "run_command"
    elif problem == "multiple_calls":
        calls.append(calls[0].copy())
    else:
        args = {"nonce": "wrong"} if problem == "wrong_nonce" else {"nonce": "0123456789abcdef", "command": "no"}
        call[key] = args if api_format == "anthropic" else json.dumps(args)
        if problem == "invalid_json":
            call[key] = "{invalid-json}"
    assert not probe._valid_tool_call(response, api_format, "0123456789abcdef")


@pytest.mark.parametrize("status,reason", [(401, "authentication_failed"), (403, "permission_denied"),
                                         (429, "rate_limited"), (503, "provider_unavailable")])
def test_auth_rate_and_service_failure_skip_other_features_safely(monkeypatch, status, reason):
    secret = "Authorization: Bearer sk-do-not-expose"
    client, _ = install_sdk(monkeypatch, behavior={"text": ProviderFailure(status, secret)})
    result = probe.probe_model(backend(), "model")
    assert len(client.requests) == 1 and client.closed
    assert result["tests"]["text"] == {"status": "failed", "reason_code": reason, "elapsed_ms": result["tests"]["text"]["elapsed_ms"]}
    assert all(result["tests"][feature]["status"] == "skipped" for feature in ["vision", "pdf", "tools"])
    assert secret not in json.dumps(result) and "sk-fixture" not in json.dumps(result)
    assert result["usage"]["calls"] == 1


def test_timeout_is_bounded_no_retry_and_prior_usage_survives(monkeypatch):
    error = type("APITimeoutError", (Exception,), {})("secret endpoint URL")
    client, _ = install_sdk(monkeypatch, behavior={"vision": error})
    result = probe.probe_model(backend(), "model")
    assert result["tests"]["vision"]["reason_code"] == "request_timeout"
    assert result["tests"]["text"]["status"] == "passed"
    assert result["tests"]["pdf"]["status"] == "skipped" and result["tests"]["tools"]["status"] == "skipped"
    assert len(client.requests) == 2 and result["usage"]["reported_calls"] == 1
    assert result["usage"]["input_tokens"] == 17 and result["usage"]["output_tokens"] == 9


@pytest.mark.parametrize("feature", ["vision", "pdf", "tools"])
def test_only_explicit_feature_rejection_is_unsupported(monkeypatch, feature):
    term = {"vision": "images", "pdf": "PDF documents", "tools": "tool use"}[feature]
    install_sdk(monkeypatch, behavior={feature: ProviderFailure(400, f"This model does not support {term}.")})
    result = probe.probe_model(backend(), "model", [feature])
    assert result["tests"][feature]["status"] == "unsupported"
    install_sdk(monkeypatch, behavior={feature: ProviderFailure(400, "Invalid request parameter")})
    result = probe.probe_model(backend(), "model", [feature])
    assert result["tests"][feature]["status"] == "failed"
    assert result["tests"][feature]["reason_code"] == "request_rejected"


def test_semantic_text_failure_is_not_false_network_failure(monkeypatch):
    client, _ = install_sdk(monkeypatch, behavior={"text": lambda response, nonce: {"choices": [{"message": {"content": "hello"}}]}})
    result = probe.probe_model(backend(), "model", ["vision"])
    assert result["tests"]["text"]["status"] == "failed"
    assert result["tests"]["vision"]["status"] == "passed"
    assert len(client.requests) == 2 and result["usage"]["reported_calls"] == 1


def test_missing_usage_is_explicit_and_close_errors_do_not_replace_results(monkeypatch):
    def remove_usage(response, nonce):
        response.pop("usage")
        return response

    client, _ = install_sdk(monkeypatch, behavior={"text": remove_usage})
    client.close = lambda: (_ for _ in ()).throw(RuntimeError("private close error"))
    result = probe.probe_model(backend(), "model", ["text"])
    assert result["tests"]["text"]["status"] == "passed"
    assert result["usage"] == {"input_tokens": 0, "output_tokens": 0, "calls": 1, "reported_calls": 0}
    assert "private close error" not in json.dumps(result)


@pytest.mark.parametrize("features", [[], "pdf", ["wrong"], [None]])
def test_empty_or_invalid_feature_selection_never_calls_sdk(monkeypatch, features):
    client, options = install_sdk(monkeypatch)
    result = probe.probe_model(backend(), "model", features)
    assert not options and not client.requests and result["usage"]["calls"] == 0
    if features == []:
        assert all(test["status"] == "skipped" for test in result["tests"].values())
    else:
        assert result["tests"]["text"]["reason_code"] == "invalid_probe_config"


@pytest.mark.parametrize("change", [{"api_format": "invalid"}, {"base_url": "file:///private"},
                                  {"base_url": "https://user:private@example.com/v1"}])
def test_invalid_config_never_calls_sdk_or_exposes_credentials(monkeypatch, change):
    client, options = install_sdk(monkeypatch)
    result = probe.probe_model({**backend(), **change}, "model")
    assert not options and not client.requests and result["usage"]["calls"] == 0
    assert all(test["reason_code"] == "invalid_probe_config" for test in result["tests"].values())
    assert "private" not in json.dumps(result)


def test_anthropic_cached_usage_is_included_in_input_total():
    assert probe._usage({"usage": {"input_tokens": 7, "output_tokens": 4,
                                  "cache_creation_input_tokens": 12, "cache_read_input_tokens": 8}}, "anthropic") == (27, 4)


def test_generic_unsupported_parameter_is_not_modality_evidence():
    error = ProviderFailure(400, "max_tokens is not supported", "unsupported_feature")
    assert probe._safe_error(error, "vision") == ("failed", "request_rejected")


@pytest.mark.parametrize("api_format", ["chat", "responses"])
def test_duplicate_json_tool_arguments_are_rejected(api_format):
    response = FakeClient(api_format).response("0123456789abcdef", "tools")
    call = response["choices"][0]["message"]["tool_calls"][0]["function"] if api_format == "chat" else response["output"][0]
    call["arguments"] = '{"nonce":"wrong","nonce":"0123456789abcdef"}'
    assert not probe._valid_tool_call(response, api_format, "0123456789abcdef")


@pytest.mark.parametrize("api_format", probe.API_FORMATS)
def test_incomplete_response_does_not_prove_capability(monkeypatch, api_format):
    def incomplete(response, nonce):
        if api_format == "chat":
            response["choices"][0]["finish_reason"] = "length"
        elif api_format == "responses":
            response["status"] = "incomplete"
        else:
            response["stop_reason"] = "max_tokens"
        return response

    install_sdk(monkeypatch, api_format, {"pdf": incomplete})
    result = probe.probe_model(backend(api_format), "model", ["pdf"])
    assert result["tests"]["pdf"]["status"] == "failed"
    assert result["tests"]["pdf"]["reason_code"] == "probe_incomplete"


@pytest.mark.parametrize("status,reason", [(401, "authentication_failed"), (429, "rate_limited")])
def test_vision_connection_failure_skips_only_later_tests(monkeypatch, status, reason):
    client, _ = install_sdk(monkeypatch, behavior={"vision": ProviderFailure(status, "private response")})
    result = probe.probe_model(backend(), "model")
    assert result["tests"]["text"]["status"] == "passed"
    assert result["tests"]["vision"]["reason_code"] == reason
    for feature in ["pdf", "tools"]:
        assert result["tests"][feature]["status"] == "skipped"
        assert result["tests"][feature]["reason_code"] == "earlier_connection_failed"
    assert len(client.requests) == 2 and result["usage"]["reported_calls"] == 1 and client.closed


def test_tuple_feature_selection_matches_application_contract(monkeypatch):
    client, _ = install_sdk(monkeypatch)
    result = probe.probe_model(backend(), "model", ("pdf",))
    assert result["tests"]["text"]["status"] == result["tests"]["pdf"]["status"] == "passed"
    assert result["tests"]["vision"]["status"] == result["tests"]["tools"]["status"] == "skipped"
    assert len(client.requests) == 2


@pytest.mark.parametrize("model", ["", "x" * 201, "model\n", "model\t", "model\x00", "model\x1f"])
def test_model_identifier_validation_matches_application(monkeypatch, model):
    client, options = install_sdk(monkeypatch)
    result = probe.probe_model(backend(), model)
    assert not options and not client.requests
    assert result["model"] == ""
    assert all(test["reason_code"] == "invalid_probe_config" for test in result["tests"].values())


@pytest.mark.parametrize("url,parameter", [
    ("https://api.openai.com/v1", "max_completion_tokens"),
    ("https://API.OPENAI.COM/v1", "max_completion_tokens"),
    ("https://gateway.example/v1", "max_tokens"),
    ("https://api.openai.com.gateway.example/v1", "max_tokens"),
    ("http://127.0.0.1:9000/v1", "max_tokens"),
])
def test_chat_output_parameter_follows_endpoint_not_model_name(monkeypatch, url, parameter):
    client, _ = install_sdk(monkeypatch)
    result = probe.probe_model({**backend(), "base_url": url}, "any-model", ["text"])
    assert result["tests"]["text"]["status"] == "passed"
    assert len(client.requests) == 1
    assert client.requests[0][parameter] == probe.MAX_OUTPUT_TOKENS
    other = "max_tokens" if parameter == "max_completion_tokens" else "max_completion_tokens"
    assert other not in client.requests[0]
