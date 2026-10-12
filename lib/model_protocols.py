"""Small, explicit adapters for the three supported text generation APIs.

The workflow keeps one internal message shape. Only this boundary translates
that shape into a provider request; unsupported content is rejected instead of
being silently dropped from a training-data prompt.
"""
from __future__ import annotations

import re
import json
from typing import Any
from urllib.parse import urlsplit

from lib.domain.model_reasoning import validate_reasoning


API_FORMATS = ("chat", "responses", "anthropic")
ANTHROPIC_DEFAULT_MAX_TOKENS = 8192


def reasoning_request(reasoning, *, api_format: str, base_url: str = "", model_info=None,
                      max_tokens=None) -> dict[str, Any]:
    """Serialize an explicit selection without silently weakening its intent."""
    choice = validate_reasoning(reasoning, max_output_tokens=max_tokens)
    mode = choice["mode"]
    if mode == "auto":
        return {}
    info = model_info or {}
    effort = choice.get("effort")
    supported = info.get("reasoning_efforts")
    if effort is not None and isinstance(supported, list) and effort not in supported:
        raise ValueError("model_reasoning_effort_unsupported")
    if mode == "disabled" and info.get("reasoning_disabled") is False:
        raise ValueError("model_reasoning_disable_unsupported")
    scheme = info.get("reasoning_format")
    # A known service is a protocol fact, not a guess based on a model name.
    if urlsplit(base_url).hostname == "api.deepseek.com":
        scheme = "deepseek"
    if "budget_tokens" in choice and (api_format != "anthropic" or scheme == "deepseek"):
        raise ValueError("model_reasoning_budget_unsupported")
    if api_format == "responses":
        if mode == "enabled" and effort is None:
            raise ValueError("model_reasoning_effort_required")
        return {"reasoning": {"effort": "none" if mode == "disabled" else effort}}
    if api_format == "chat":
        if scheme == "deepseek":
            result = {"extra_body": {"thinking": {"type": mode}}}
            if effort is not None:
                result["reasoning_effort"] = effort
            return result
        if mode == "enabled" and effort is None:
            raise ValueError("model_reasoning_effort_required")
        return {"reasoning_effort": "none" if mode == "disabled" else effort}
    if api_format != "anthropic":
        raise ValueError("invalid_api_format")
    if mode == "disabled":
        return {"thinking": {"type": "disabled"}}
    if "budget_tokens" in choice:
        if scheme == "anthropic_adaptive":
            raise ValueError("model_reasoning_budget_unsupported")
        return {"thinking": {"type": "enabled", "budget_tokens": choice["budget_tokens"]}}
    if scheme not in {"deepseek", "anthropic_adaptive"}:
        raise ValueError("model_reasoning_format_unknown")
    result = {"thinking": {"type": "enabled" if scheme == "deepseek" else "adaptive"}}
    if effort is not None:
        # extra_body works on older Anthropic SDKs as well as newer ones; the
        # field still lives at the top level of the actual HTTP request.
        result["extra_body"] = {"output_config": {"effort": effort}}
    return result


def validate_api_format(value: str) -> str:
    if value not in API_FORMATS:
        raise ValueError("invalid_api_format")
    return value


def _calls(message):
    calls = message.get("tool_calls", [])
    if not isinstance(calls, list):
        raise ValueError("invalid_model_tool_call")
    result = []
    for call in calls:
        if not isinstance(call, dict) or not isinstance(call.get("function"), dict):
            raise ValueError("invalid_model_tool_call")
        function = call["function"]
        identity = call.get("id")
        name, arguments = function.get("name"), function.get("arguments")
        if (call.get("type") != "function" or not isinstance(identity, str) or not identity
                or not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name)
                or not isinstance(arguments, str)):
            raise ValueError("invalid_model_tool_call")
        try:
            parsed = json.loads(arguments)
        except (TypeError, ValueError) as error:
            raise ValueError("invalid_model_tool_arguments") from error
        if not isinstance(parsed, dict):
            raise ValueError("invalid_model_tool_arguments")
        result.append((identity, name, arguments, parsed))
    return result


def _paired_tools(messages):
    """Reject missing/duplicate results before crossing a provider boundary."""
    pending, seen = {}, set()
    for message in messages:
        role = message.get("role")
        calls = _calls(message)
        if calls and role != "assistant":
            raise ValueError("invalid_model_tool_call")
        if role == "tool":
            identity = message.get("tool_call_id", message.get("toolCallId"))
            if not isinstance(identity, str) or identity not in pending or not isinstance(message.get("content"), str):
                raise ValueError("orphan_model_tool_result")
            name = message.get("name", message.get("toolName"))
            if name is not None and name != pending[identity]:
                raise ValueError("model_tool_result_name_mismatch")
            del pending[identity]
        elif pending:
            raise ValueError("missing_model_tool_result")
        for identity, name, _, _ in calls:
            if identity in seen:
                raise ValueError("duplicate_model_tool_call")
            seen.add(identity)
            pending[identity] = name
    if pending:
        raise ValueError("missing_model_tool_result")


def _image_url(part: dict[str, Any]) -> str:
    image = part.get("image_url")
    url = image.get("url") if isinstance(image, dict) else image
    if not isinstance(url, str) or not url:
        raise ValueError("unsupported_model_image")
    return url


def responses_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map Chat-style messages to Responses input items, including images."""
    _paired_tools(messages)
    converted = []
    for message in messages:
        role = message.get("role")
        if role == "tool":
            converted.append({"type": "function_call_output",
                "call_id": message.get("tool_call_id", message.get("toolCallId")),
                "output": message["content"]})
            continue
        calls = _calls(message)
        if calls:
            content = message.get("content")
            if content:
                if not isinstance(content, str):
                    raise ValueError("unsupported_model_message_content")
                converted.append({"role": "assistant", "content": content})
            converted.extend({"type": "function_call", "call_id": identity,
                "name": name, "arguments": arguments} for identity, name, arguments, _ in calls)
            continue
        if role not in {"system", "developer", "user", "assistant"}:
            raise ValueError("unsupported_model_message_role")
        content = message.get("content")
        if isinstance(content, str):
            converted.append({"role": role, "content": content})
            continue
        if not isinstance(content, list):
            raise ValueError("unsupported_model_message_content")
        parts = []
        for part in content:
            if not isinstance(part, dict):
                raise ValueError("unsupported_model_message_content")
            if part.get("type") in {"text", "input_text"} and isinstance(part.get("text"), str):
                parts.append({"type": "input_text", "text": part["text"]})
            elif part.get("type") == "image_url" and role == "user":
                parts.append({"type": "input_image", "image_url": _image_url(part)})
            else:
                raise ValueError("unsupported_model_message_content")
        converted.append({"role": role, "content": parts})
    return converted


def anthropic_request(messages: list[dict[str, Any]], *, json_mode: bool) -> dict[str, Any]:
    """Move system instructions to the top level and translate image blocks."""
    _paired_tools(messages)
    system = []
    conversation = []
    tool_names = set()
    if json_mode:
        system.append("Return only a valid JSON object.")
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role == "tool":
            block = {"type": "tool_result",
                "tool_use_id": message.get("tool_call_id", message.get("toolCallId")),
                "content": content}
            if conversation and conversation[-1]["role"] == "user" and isinstance(conversation[-1]["content"], list):
                conversation[-1]["content"].append(block)
            else:
                conversation.append({"role": "user", "content": [block]})
            continue
        calls = _calls(message)
        if calls:
            parts = []
            if content:
                if not isinstance(content, str):
                    raise ValueError("unsupported_model_message_content")
                parts.append({"type": "text", "text": content})
            for identity, name, _, parsed in calls:
                parts.append({"type": "tool_use", "id": identity, "name": name, "input": parsed})
                tool_names.add(name)
            conversation.append({"role": "assistant", "content": parts})
            continue
        if role in {"system", "developer"}:
            if not isinstance(content, str):
                raise ValueError("unsupported_model_message_content")
            system.append(content)
            continue
        if role not in {"user", "assistant"}:
            raise ValueError("unsupported_model_message_role")
        if isinstance(content, str):
            if conversation and conversation[-1]["role"] == role and isinstance(conversation[-1]["content"], list):
                conversation[-1]["content"].append({"type": "text", "text": content})
                continue
            conversation.append({"role": role, "content": content})
            continue
        if not isinstance(content, list):
            raise ValueError("unsupported_model_message_content")
        parts = []
        for part in content:
            if not isinstance(part, dict):
                raise ValueError("unsupported_model_message_content")
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                parts.append({"type": "text", "text": part["text"]})
            elif part.get("type") == "image_url" and role == "user":
                match = re.fullmatch(r"data:(image/(?:png|jpeg|webp|gif));base64,([A-Za-z0-9+/=]+)", _image_url(part))
                if not match:
                    raise ValueError("unsupported_anthropic_image_url")
                parts.append({"type": "image", "source": {
                    "type": "base64", "media_type": match.group(1), "data": match.group(2),
                }})
            else:
                raise ValueError("unsupported_model_message_content")
        conversation.append({"role": role, "content": parts})
    if not conversation:
        raise ValueError("missing_model_user_message")
    request: dict[str, Any] = {"messages": conversation}
    if system:
        request["system"] = "\n\n".join(system)
    if tool_names:
        request["tools"] = [{"name": name, "description": "Read-only source memory already retrieved by the workflow.",
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": True}}
            for name in sorted(tool_names)]
        request["tool_choice"] = {"type": "none"}
    return request


def response_text(response: Any) -> str:
    """Read Responses output_text or its equivalent output blocks."""
    value = getattr(response, "output_text", None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    chunks = []
    for item in getattr(response, "output", ()) or ():
        if getattr(item, "type", None) != "message":
            continue
        for block in getattr(item, "content", ()) or ():
            if getattr(block, "type", None) == "output_text" and isinstance(getattr(block, "text", None), str):
                chunks.append(block.text)
    return "\n".join(chunks).strip()


def anthropic_text(response: Any) -> str:
    return "\n".join(
        block.text for block in (getattr(response, "content", None) or ())
        if getattr(block, "type", None) == "text" and isinstance(getattr(block, "text", None), str)
    ).strip()
