"""Small, explicit adapters for the three supported text generation APIs.

The workflow keeps one internal message shape. Only this boundary translates
that shape into a provider request; unsupported content is rejected instead of
being silently dropped from a training-data prompt.
"""
from __future__ import annotations

import re
from typing import Any


API_FORMATS = ("chat", "responses", "anthropic")
ANTHROPIC_DEFAULT_MAX_TOKENS = 8192


def validate_api_format(value: str) -> str:
    if value not in API_FORMATS:
        raise ValueError("invalid_api_format")
    return value


def _tool_result(message: dict[str, Any]) -> dict[str, str]:
    name = str(message.get("name") or message.get("toolName") or "tool")
    call_id = str(message.get("toolCallId") or "")
    label = f"Tool result ({name}{', ' + call_id if call_id else ''}): "
    content = message.get("content")
    if not isinstance(content, str):
        raise ValueError("unsupported_model_message_content")
    return {"role": "user", "content": label + content}


def _image_url(part: dict[str, Any]) -> str:
    image = part.get("image_url")
    url = image.get("url") if isinstance(image, dict) else image
    if not isinstance(url, str) or not url:
        raise ValueError("unsupported_model_image")
    return url


def responses_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map Chat-style messages to Responses input items, including images."""
    converted = []
    for original in messages:
        message = _tool_result(original) if original.get("role") == "tool" else original
        role = message.get("role")
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
    system = []
    conversation = []
    if json_mode:
        system.append("Return only a valid JSON object.")
    for original in messages:
        message = _tool_result(original) if original.get("role") == "tool" else original
        role = message.get("role")
        content = message.get("content")
        if role in {"system", "developer"}:
            if not isinstance(content, str):
                raise ValueError("unsupported_model_message_content")
            system.append(content)
            continue
        if role not in {"user", "assistant"}:
            raise ValueError("unsupported_model_message_role")
        if isinstance(content, str):
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
