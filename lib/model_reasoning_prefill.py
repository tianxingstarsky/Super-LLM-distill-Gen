"""Explicit provider-supported reasoning prefill, never a dialogue fallback.

DeepSeek documents ``reasoning_content`` as the last assistant's CoT input
for Chat Prefix Completion. This is a reasoning prefix, not proof that the
provider will stop thinking or adopt an authored trace as its native output.
https://api-docs.deepseek.com/api/create-chat-completion/
https://api-docs.deepseek.com/guides/chat_prefix_completion/
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
from urllib.parse import urlsplit


DEEPSEEK_PREFILL_BASE_URL = "https://api.deepseek.com/beta"


def validate_prefill_client(client, *, thinking=None):
    """Check known protocol conditions without fetching or probing a model."""
    prepare_reasoning_prefill([{"role": "user", "content": "."}], ".",
        api_format=getattr(client, "api_format", None),
        base_url=getattr(client, "base_url", ""),
        reasoning=getattr(client, "reasoning", None), thinking=thinking)


def prepare_reasoning_prefill(messages, reasoning_prefix, *, api_format,
                              base_url, reasoning=None, thinking=None):
    """Validate before admission, then append an immutable assistant prefix.

    Ordinary historical native reasoning remains historical reasoning. A
    caller cannot bypass this explicit capability through a message flag.
    """
    if reasoning_prefix is not None and (not isinstance(messages, list) or not messages
            or any(not isinstance(message, dict) for message in messages)):
        raise ValueError("invalid_model_reasoning_prefill_messages")
    if any(isinstance(message, dict) and "prefix" in message for message in messages):
        raise ValueError("model_reasoning_prefill_explicit_required")
    if reasoning_prefix is None:
        return messages, {}
    if not isinstance(reasoning_prefix, str) or not reasoning_prefix.strip():
        raise ValueError("invalid_model_reasoning_prefix")
    try:
        prefix_bytes = reasoning_prefix.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("invalid_model_reasoning_prefix") from None
    try:
        endpoint = urlsplit(base_url)
        supported = (api_format == "chat" and endpoint.scheme == "https"
            and endpoint.hostname == "api.deepseek.com"
            and endpoint.port in (None, 443) and not endpoint.username
            and not endpoint.password and not endpoint.query and not endpoint.fragment
            and endpoint.path.rstrip("/") in {"", "/v1", "/beta"})
    except (TypeError, ValueError, AttributeError):
        supported = False
    if not supported:
        raise ValueError("model_reasoning_prefill_unsupported")
    mode = (reasoning or {}).get("mode", "auto")
    if mode == "disabled" or (mode == "auto" and thinking is False):
        raise ValueError("model_reasoning_prefill_thinking_disabled")
    if any(message.get("role") == "tool" or "tool_calls" in message
           or "function_call" in message for message in messages):
        # DeepSeek beta rejects function-call history alongside a prefix.
        # Callers must choose an explicitly labelled source transport before
        # building the request; this boundary never drops or rewrites tools.
        raise ValueError("model_reasoning_prefill_tools_unsupported")
    prepared = [*deepcopy(messages), {"role": "assistant", "content": "",
        "prefix": True, "reasoning_content": reasoning_prefix}]
    return prepared, {"reasoning_prefill": {"version": 1,
        "transport": "deepseek_chat_beta",
        "prefix_sha256": hashlib.sha256(prefix_bytes).hexdigest(),
        "prefix_utf8_bytes": len(prefix_bytes),
        "response_reasoning": "provider_continuation"}}
