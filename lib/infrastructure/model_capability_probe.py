"""Small, explicit SDK probes with verifiable, synthetic content.

These requests test the selected connection, not the model's maximum capacity.
Only synthetic fixtures leave the process. No tool is executed, no file is
uploaded to a Files API, and the SDK never retries a billable request.

Wire formats follow the providers' official file-input and tool-use docs:
https://developers.openai.com/api/docs/guides/file-inputs
https://developers.openai.com/api/docs/guides/function-calling
https://platform.claude.com/docs/en/build-with-claude/pdf-support
https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools
"""
from __future__ import annotations

import base64
from datetime import datetime, timezone
from io import BytesIO
import json
import os
import re
import secrets
import time
from typing import Any
from urllib.parse import urlsplit

from lib.model_protocols import API_FORMATS


FEATURES = ("text", "vision", "pdf", "tools")
PROBE_TIMEOUT_SECONDS = 12.0
MAX_OUTPUT_TOKENS = 256
# The caller may reserve this conservative bound, then settle reported usage.
MAX_INPUT_TOKENS_PER_PROBE = 4096
_TOOL_NAME = "confirm_probe"
_COLORS = (
    ("red", (255, 0, 0)), ("green", (0, 160, 0)), ("blue", (0, 0, 255)),
    ("yellow", (255, 230, 0)), ("purple", (128, 0, 128)), ("orange", (255, 128, 0)),
)
_BLOCKING_REASONS = {
    "authentication_failed", "permission_denied", "rate_limited", "request_timeout",
    "connection_failed", "provider_unavailable", "model_not_found", "request_rejected",
    "unexpected_probe_error", "sdk_unavailable",
}


def _value(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _pdf_fixture(nonce: str) -> bytes:
    """Build one valid PDF page without a compiler, disk access, or dependencies."""
    if not re.fullmatch(r"[a-f0-9]{16}", nonce):
        raise ValueError("invalid_probe_nonce")
    stream = f"BT /F1 16 Tf 20 64 Td ({nonce}) Tj ET\n".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 256 128] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"endstream",
    ]
    result = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, content in enumerate(objects, 1):
        offsets.append(len(result))
        result.extend(f"{number} 0 obj\n".encode("ascii") + content + b"\nendobj\n")
    xref = len(result)
    result.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        result.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    result.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
    return bytes(result)


def _image_fixture() -> tuple[bytes, str]:
    from PIL import Image

    color, rgb = secrets.choice(_COLORS)
    image = Image.new("RGB", (96, 96), rgb)
    output = BytesIO()
    image.save(output, format="PNG")
    image.close()
    return output.getvalue(), color


def _make_client(backend: dict, api_format: str) -> Any:
    default_env = "ANTHROPIC_API_KEY" if api_format == "anthropic" else "OPENAI_API_KEY"
    env_name = backend.get("api_key_env")
    env_name = default_env if env_name is None else str(env_name)
    api_key = backend.get("api_key") or os.environ.get(env_name, "") or "sk-local"
    options = {"base_url": backend["base_url"], "api_key": api_key,
               "timeout": PROBE_TIMEOUT_SECONDS, "max_retries": 0}
    if api_format == "anthropic":
        from anthropic import Anthropic

        return Anthropic(**options)
    from openai import OpenAI

    return OpenAI(**options)


def _request(client: Any, api_format: str, model: str, feature: str, nonce: str, *,
             official_openai_chat: bool = False) -> tuple[Any, str]:
    expected = nonce
    if feature == "text":
        prompt = f"Reply with exactly this verification code and no other text: {nonce}"
        part = None
    elif feature == "vision":
        image, expected = _image_fixture()
        encoded = base64.b64encode(image).decode("ascii")
        prompt = "What is the dominant color in the attached image? Reply with one common English color word only."
        if api_format == "anthropic":
            part = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": encoded}}
        else:
            part = {"type": "input_image" if api_format == "responses" else "image_url",
                    "image_url": f"data:image/png;base64,{encoded}"}
            if api_format == "chat":
                part["image_url"] = {"url": part["image_url"], "detail": "low"}
            else:
                part["detail"] = "low"
    elif feature == "pdf":
        encoded = base64.b64encode(_pdf_fixture(nonce)).decode("ascii")
        prompt = "Read the attached PDF. Reply with exactly the verification code printed on its page, and no other text."
        if api_format == "anthropic":
            part = {"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": encoded}}
        else:
            file = {"filename": "probe.pdf", "file_data": f"data:application/pdf;base64,{encoded}"}
            part = {"type": "input_file", **file} if api_format == "responses" else {"type": "file", "file": file}
    else:
        prompt = f"Call the confirm_probe function once with nonce equal to {nonce}. Do not reply in text."
        part = None

    text_type = "input_text" if api_format == "responses" else "text"
    content = [{"type": text_type, "text": prompt}]
    if part is not None:
        content.insert(0, part)
    kwargs: dict[str, Any] = {"model": model, "stream": False, "timeout": PROBE_TIMEOUT_SECONDS}
    if api_format == "responses":
        kwargs.update(input=[{"role": "user", "content": content}], max_output_tokens=MAX_OUTPUT_TOKENS, store=False)
    else:
        kwargs["messages"] = [{"role": "user", "content": content}]
        # Official Chat supports this parameter for both reasoning and regular
        # models. Compatible gateways retain their existing max_tokens contract.
        # Endpoint identity avoids model-name guessing or a second paid request.
        output_parameter = "max_completion_tokens" if api_format == "chat" and official_openai_chat else "max_tokens"
        kwargs[output_parameter] = MAX_OUTPUT_TOKENS
    if feature == "tools":
        schema = {"type": "object", "properties": {"nonce": {"type": "string"}},
                  "required": ["nonce"], "additionalProperties": False}
        description = "Return the supplied verification nonce. This test function has no side effects and will not be executed."
        if api_format == "anthropic":
            kwargs["tools"] = [{"name": _TOOL_NAME, "description": description, "input_schema": schema}]
            kwargs["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": True}
        else:
            function = {"name": _TOOL_NAME, "description": description, "parameters": schema}
            kwargs["tools"] = [{"type": "function", **function}] if api_format == "responses" else [{"type": "function", "function": function}]
            kwargs["tool_choice"] = "auto"
    if api_format == "anthropic":
        response = client.messages.create(**kwargs)
    elif api_format == "responses":
        response = client.responses.create(**kwargs)
    else:
        response = client.chat.completions.create(**kwargs)
    return response, expected


def _text(response: Any, api_format: str) -> str:
    if api_format == "chat":
        choices = _value(response, "choices", []) or []
        result = _value(_value(choices[0], "message"), "content") if choices else ""
        return result.strip()[:8192] if isinstance(result, str) else ""
    if api_format == "responses":
        result = _value(response, "output_text")
        if isinstance(result, str) and result.strip():
            return result.strip()[:8192]
        blocks = [block for item in (_value(response, "output", []) or [])
                  if _value(item, "type") == "message" for block in (_value(item, "content", []) or [])]
        block_type = "output_text"
    else:
        blocks, block_type = _value(response, "content", []) or [], "text"
    return "\n".join(_value(block, "text", "") for block in blocks
                     if _value(block, "type") == block_type and isinstance(_value(block, "text"), str)).strip()[:8192]


def _valid_tool_call(response: Any, api_format: str, nonce: str) -> bool:
    if api_format == "chat":
        choices = _value(response, "choices", []) or []
        calls = _value(_value(choices[0], "message"), "tool_calls", []) if choices else []
        calls = [call for call in (calls or []) if _value(call, "type") == "function"]
        function = _value(calls[0], "function") if len(calls) == 1 else None
        name, arguments = _value(function, "name"), _value(function, "arguments")
    else:
        output = _value(response, "output" if api_format == "responses" else "content", []) or []
        calls = [item for item in output if _value(item, "type") == ("function_call" if api_format == "responses" else "tool_use")]
        call = calls[0] if len(calls) == 1 else None
        name = _value(call, "name")
        arguments = _value(call, "arguments" if api_format == "responses" else "input")
    if name != _TOOL_NAME:
        return False
    if api_format != "anthropic":
        if not isinstance(arguments, str) or len(arguments) > 4096:
            return False
        try:
            def unique_keys(pairs):
                values = {}
                for key, value in pairs:
                    if key in values:
                        raise ValueError("duplicate_tool_argument")
                    values[key] = value
                return values

            arguments = json.loads(arguments, object_pairs_hook=unique_keys)
        except (ValueError, TypeError):
            return False
    return isinstance(arguments, dict) and arguments == {"nonce": nonce}


def _safe_error(error: Exception, feature: str) -> tuple[str, str]:
    """Use only stable codes outside this boundary; never return error text/body."""
    code = _value(error, "status_code")
    kind = type(error).__name__
    if code == 401 or kind == "AuthenticationError":
        return "failed", "authentication_failed"
    if code == 403 or kind == "PermissionDeniedError":
        return "failed", "permission_denied"
    if code == 429 or kind == "RateLimitError":
        return "failed", "rate_limited"
    if kind in {"APITimeoutError", "TimeoutError", "ReadTimeout", "ConnectTimeout", "WriteTimeout", "PoolTimeout"}:
        return "failed", "request_timeout"
    if kind in {"APIConnectionError", "ConnectError", "ConnectionError", "NetworkError"}:
        return "failed", "connection_failed"
    if isinstance(code, int) and code >= 500:
        return "failed", "provider_unavailable"
    if isinstance(error, ImportError):
        return "failed", "sdk_unavailable"
    body = _value(error, "body", {})
    detail = body.get("error", body) if isinstance(body, dict) else {}
    detail = detail if isinstance(detail, dict) else {}
    provider_code = detail.get("code") or detail.get("type")
    if provider_code in {"model_not_found", "model_not_available"}:
        return "failed", "model_not_found"
    # Explicit feature rejection is useful; a generic 400 is not proof of it.
    if code in {400, 415, 422} and feature != "text":
        terms = {"vision": ("image", "vision"), "pdf": ("pdf", "file input", "document"),
                 "tools": ("tool", "function calling")}[feature]
        message = str(detail.get("message") or _value(error, "message", "")).lower()[:4096]
        if provider_code in {"unsupported_content_type", "unsupported_modality"} or (
            any(term in message for term in terms)
            and any(phrase in message for phrase in ("not supported", "does not support", "unsupported", "not support"))
        ):
            return "unsupported", "feature_rejected"
    if isinstance(code, int) and 400 <= code < 500:
        return "failed", "request_rejected"
    return "failed", "unexpected_probe_error"


def _usage(response: Any, api_format: str) -> tuple[int, int] | None:
    usage = _value(response, "usage")
    incoming = _value(usage, "prompt_tokens" if api_format == "chat" else "input_tokens")
    outgoing = _value(usage, "completion_tokens" if api_format == "chat" else "output_tokens")
    if not isinstance(incoming, int) or isinstance(incoming, bool) or not isinstance(outgoing, int) or isinstance(outgoing, bool):
        return None
    if incoming < 0 or outgoing < 0:
        return None
    if api_format == "anthropic":
        for field in ("cache_creation_input_tokens", "cache_read_input_tokens"):
            cached = _value(usage, field, 0)
            if isinstance(cached, int) and not isinstance(cached, bool) and cached > 0:
                incoming += cached
    return incoming, outgoing


def _incomplete_reply(response: Any, api_format: str) -> bool:
    if api_format == "responses":
        return _value(response, "status") not in {None, "completed"}
    if api_format == "anthropic":
        return _value(response, "stop_reason") in {"max_tokens", "refusal", "pause_turn"}
    choices = _value(response, "choices", []) or []
    return bool(choices) and _value(choices[0], "finish_reason") in {"length", "content_filter"}


def probe_model(backend: dict, model: str, features: list[str] | tuple[str, ...] | None = None) -> dict:
    """Explicitly test one model; callers decide when to run and how to budget it."""
    start = time.monotonic()
    api_format = backend.get("api_format", "chat") if isinstance(backend, dict) else "unknown"
    selected = list(FEATURES) if features is None else features
    valid_features = isinstance(selected, (list, tuple)) and all(isinstance(item, str) and item in FEATURES for item in selected)
    selected = set(selected) if valid_features else set()
    if selected:
        selected.add("text")
    valid_model = (type(model) is str and bool(model.strip()) and len(model) <= 200
                   and all(ord(character) >= 32 for character in model))
    result = {"model": model.strip() if valid_model else "", "api_format": api_format if api_format in API_FORMATS else "unknown",
              "tests": {feature: {"status": "skipped", "reason_code": "not_requested", "elapsed_ms": 0} for feature in FEATURES},
              "tested_at": datetime.now(timezone.utc).isoformat(),
              "usage": {"input_tokens": 0, "output_tokens": 0, "calls": 0, "reported_calls": 0}}
    client = None
    try:
        parsed = urlsplit(backend.get("base_url", "")) if isinstance(backend, dict) else None
        if not valid_features or not valid_model or api_format not in API_FORMATS or parsed is None or parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            for feature in (selected or {"text"}):
                result["tests"][feature] = {"status": "failed", "reason_code": "invalid_probe_config", "elapsed_ms": 0}
            return result
        if not selected:
            return result
        client = _make_client(backend, api_format)
        for feature in FEATURES:
            if feature not in selected:
                continue
            test_start = time.monotonic()
            try:
                nonce = secrets.token_hex(8)
                result["usage"]["calls"] += 1
                response, expected = _request(client, api_format, model.strip(), feature, nonce,
                                              official_openai_chat=parsed.hostname == "api.openai.com")
                usage = _usage(response, api_format)
                if usage is not None:
                    result["usage"]["input_tokens"] += usage[0]
                    result["usage"]["output_tokens"] += usage[1]
                    result["usage"]["reported_calls"] += 1
                if feature == "tools":
                    passed = _valid_tool_call(response, api_format, nonce)
                else:
                    reply = _text(response, api_format)
                    if feature == "vision":
                        reply = reply.lower().strip(". \n\t\"'")
                    passed = reply == expected
                incomplete = _incomplete_reply(response, api_format)
                if incomplete:
                    passed = False
                test = {"status": "passed" if passed else "failed",
                        "reason_code": "content_verified" if passed else "probe_incomplete" if incomplete else "probe_reply_mismatch"}
                if passed:
                    test["evidence"] = {"text": "nonce_echo", "vision": "image_content_verified",
                                        "pdf": "pdf_content_verified", "tools": "function_call_verified"}[feature]
            except Exception as error:
                status, reason = _safe_error(error, feature)
                test = {"status": status, "reason_code": reason}
            test["elapsed_ms"] = max(0, round((time.monotonic() - test_start) * 1000))
            result["tests"][feature] = test
            if test["reason_code"] in _BLOCKING_REASONS:
                for remaining in FEATURES[FEATURES.index(feature) + 1:]:
                    if remaining in selected:
                        result["tests"][remaining] = {"status": "skipped", "reason_code": "earlier_connection_failed", "elapsed_ms": 0}
                break
    except Exception as error:
        _, reason = _safe_error(error, "text")
        result["tests"]["text"] = {"status": "failed", "reason_code": reason, "elapsed_ms": 0}
        for feature in selected - {"text"}:
            result["tests"][feature] = {"status": "skipped", "reason_code": "earlier_connection_failed", "elapsed_ms": 0}
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass
        result["elapsed_ms"] = max(0, round((time.monotonic() - start) * 1000))
    return result
