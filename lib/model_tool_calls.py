"""One real function-calling turn on the client's admitted SDK connection.

This channel does not execute tools or accept native CoT beta prefixes. Callers
persist the returned assistant dictionary unchanged, execute their own allowed
tools, then append paired ``role=tool`` results before requesting the next turn.
Opaque provider state is replayed, never promoted to visible answer text.
"""
from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
import json
import math
import re
import time
from urllib.parse import urlsplit

from lib.model_protocols import (
    ANTHROPIC_DEFAULT_MAX_TOKENS, _calls, _paired_tools, anthropic_request,
    reasoning_request, responses_input,
)
from lib.model_request_reliability import (
    ModelRequestError, SERVICE_COOLDOWNS, cancelable_wait, classify_request_error,
    rejected_before_generation, retry_delay,
)
from lib.model_response_details import commit_response_details, extract_response_details
from lib.model_streaming import ModelStreamError


MAX_TOOLS = 128


def _get(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _plain(value):
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if hasattr(value, "__dict__"):
        return {key: _plain(item) for key, item in vars(value).items() if not key.startswith("_")}
    return value


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("invalid_model_tool_json") from None


def tool_context_accounting(messages, tools):
    """Exactly the conservative continuity accounting used by tool admission."""
    return [*messages, {"role": "system", "content": _json({"tools": tools,
        "continuity": [{key: message[key] for key in ("reasoning_content", "provider_state")
                        if key in message} for message in messages]})}]


def tool_context_input_bytes(messages, tools):
    """Text upper bound matching ChatClient._check_context, without admission."""
    parts = []
    for message in tool_context_accounting(messages, tools):
        content = message.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(part.get("text", "") for part in content
                         if isinstance(part, dict) and isinstance(part.get("text"), str))
        if message.get("tool_calls"):
            parts.append(json.dumps(message["tool_calls"], ensure_ascii=False))
        if message.get("prefix") is True:
            parts.append(message["reasoning_content"])
    return sum(len(part.encode("utf-8")) for part in parts) + (
        1024 if any(message.get("role") == "tool" for message in messages) else 0)


def _tools(value):
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_TOOLS:
        raise ValueError("invalid_model_tools")
    result, names = [], set()
    for tool in value:
        if not isinstance(tool, dict) or set(tool) != {"type", "function"} or tool["type"] != "function":
            raise ValueError("invalid_model_tools")
        fn = tool["function"]
        if not isinstance(fn, dict) or set(fn) - {"name", "description", "parameters", "strict"}:
            raise ValueError("invalid_model_tools")
        name, schema = fn.get("name"), fn.get("parameters")
        if (not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name)
                or name in names or not isinstance(schema, dict) or schema.get("type") != "object"
                or ("description" in fn and not isinstance(fn["description"], str))
                or ("strict" in fn and type(fn["strict"]) is not bool)):
            raise ValueError("invalid_model_tools")
        _json(tool)
        names.add(name)
        result.append(deepcopy(tool))
    return result


def _call(identity, name, arguments):
    call = {"id": identity, "type": "function", "function": {"name": name, "arguments": arguments}}
    _calls({"tool_calls": [call]})
    return call


def _block_assistant(blocks, api):
    """Normalize while retaining complete signed/encrypted response blocks."""
    if not isinstance(blocks, list) or any(not isinstance(block, dict) for block in blocks):
        raise ValueError("invalid_model_tool_provider_state")
    text, calls = [], []
    for block in blocks:
        kind = block.get("type")
        if api == "responses":
            if block.get("status") not in (None, "completed"):
                raise ModelStreamError("model_stream_incomplete")
            if kind == "function_call":
                calls.append(_call(block.get("call_id"), block.get("name"), block.get("arguments")))
            elif kind == "message":
                if (block.get("role") != "assistant" or not isinstance(block.get("content"), list)
                        or any(not isinstance(part, dict) for part in block["content"])):
                    raise ValueError("invalid_model_tool_provider_state")
                for part in block["content"]:
                    if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                        text.append(part["text"])
                    elif part.get("type") != "refusal":
                        raise ValueError("unsupported_model_tool_output")
            elif kind != "reasoning":
                raise ValueError("unsupported_model_tool_output")
        elif kind == "text" and isinstance(block.get("text"), str):
            text.append(block["text"])
        elif kind == "tool_use":
            if not isinstance(block.get("input"), dict):
                raise ValueError("invalid_model_tool_arguments")
            calls.append(_call(block.get("id"), block.get("name"), _json(block["input"])))
        elif kind not in {"thinking", "redacted_thinking"}:
            raise ValueError("unsupported_model_tool_output")
    identities = [call["id"] for call in calls]
    if len(set(identities)) != len(identities):
        raise ValueError("duplicate_model_tool_call")
    return {"role": "assistant", "content": "".join(text), "tool_calls": calls}


def _state(message, api):
    state = message.get("provider_state")
    if state is None:
        if api != "chat" and message.get("reasoning_content"):
            # Signed/encrypted thinking cannot be reconstructed from plain text.
            raise ValueError("model_tool_reasoning_history_unsupported")
        return None
    if not isinstance(state, dict) or state.get("api_format") != api or message.get("role") != "assistant":
        raise ValueError("invalid_model_tool_provider_state")
    if api == "chat":
        return state
    key = "output_items" if api == "responses" else "content_blocks"
    normalized = _block_assistant(state.get(key), api)
    if (normalized["content"] != message.get("content", "")
            or normalized["tool_calls"] != message.get("tool_calls", [])):
        raise ValueError("model_tool_history_state_mismatch")
    return state


def _history(messages, api):
    if (not isinstance(messages, list) or not messages
            or any(not isinstance(message, dict) for message in messages)):
        raise ValueError("invalid_model_tool_messages")
    if any("prefix" in message for message in messages):
        raise ValueError("model_tool_turn_native_prefill_unsupported")
    _paired_tools(messages)
    for message in messages:
        if message.get("role") not in {"system", "developer", "user", "assistant", "tool"}:
            raise ValueError("unsupported_model_message_role")
        if "function_call" in message:
            raise ValueError("invalid_model_tool_call")
        if "reasoning_content" in message and (message.get("role") != "assistant"
                or not isinstance(message["reasoning_content"], str)):
            raise ValueError("invalid_model_tool_reasoning")
        _state(message, api)
    result = deepcopy(messages)
    for message in result:
        if message.get("role") == "tool":
            message["tool_call_id"] = message.get("tool_call_id", message.get("toolCallId"))
            if "name" not in message and "toolName" in message:
                message["name"] = message["toolName"]
    return result


def _wire_messages(messages, api):
    if api == "chat":
        result = []
        for message in messages:
            state = _state(message, api)
            item = {key: deepcopy(value) for key, value in message.items()
                    if key in {"role", "content", "tool_calls", "tool_call_id", "name", "reasoning_content"}}
            if state and "reasoning_details" in state:
                item["reasoning_details"] = deepcopy(state["reasoning_details"])
            result.append(item)
        return {"messages": result}
    if api == "responses":
        result = []
        for message in messages:
            state = _state(message, api)
            if state:
                result.extend(deepcopy(state["output_items"]))
            elif message["role"] == "tool":
                result.append({"type": "function_call_output", "call_id": message["tool_call_id"],
                               "output": message["content"]})
            elif message.get("tool_calls"):
                if message.get("content"):
                    result.append({"role": "assistant", "content": message["content"]})
                result.extend({"type": "function_call", "call_id": identity, "name": name,
                               "arguments": arguments} for identity, name, arguments, _ in _calls(message))
            else:
                result.extend(responses_input([message]))
        return {"input": result, "store": False}
    system, conversation = [], []
    for message in messages:
        state = _state(message, api)
        role, content = message["role"], message.get("content", "")
        if role in {"system", "developer"}:
            if not isinstance(content, str):
                raise ValueError("unsupported_model_message_content")
            system.append(content)
        elif role == "tool":
            block = {"type": "tool_result", "tool_use_id": message["tool_call_id"], "content": content}
            if "is_error" in message:
                if type(message["is_error"]) is not bool:
                    raise ValueError("invalid_model_tool_result")
                block["is_error"] = message["is_error"]
            if conversation and conversation[-1]["role"] == "user" and isinstance(conversation[-1]["content"], list):
                conversation[-1]["content"].append(block)
            else:
                conversation.append({"role": "user", "content": [block]})
        elif state:
            conversation.append({"role": "assistant", "content": deepcopy(state["content_blocks"])})
        elif message.get("tool_calls"):
            parts = [{"type": "text", "text": content}] if content else []
            parts.extend({"type": "tool_use", "id": identity, "name": name, "input": parsed}
                         for identity, name, _, parsed in _calls(message))
            conversation.append({"role": "assistant", "content": parts})
        else:
            conversation.extend(anthropic_request([message], json_mode=False)["messages"])
    request = {"messages": conversation}
    if system:
        request["system"] = "\n\n".join(system)
    return request


def _request_kwargs(client, messages, tools, max_tokens, temperature):
    api = client.api_format
    kwargs = {"model": client.model, **_wire_messages(messages, api), "temperature": temperature}
    kwargs.update(reasoning_request(client.reasoning, api_format=api, base_url=client.base_url,
                                   model_info=client.model_info, max_tokens=max_tokens))
    if api == "chat":
        kwargs.update(tools=tools, tool_choice="auto")
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
    elif api == "responses":
        kwargs.update(tools=[{"type": "function", "strict": False, **deepcopy(tool["function"])} for tool in tools],
                      tool_choice="auto")
        if max_tokens is not None:
            kwargs["max_output_tokens"] = max_tokens
    else:
        kwargs.update(tools=[{"name": tool["function"]["name"],
            "input_schema": deepcopy(tool["function"]["parameters"]),
            **{key: tool["function"][key] for key in ("description", "strict")
               if key in tool["function"]}} for tool in tools], tool_choice={"type": "auto"},
            max_tokens=max_tokens or ANTHROPIC_DEFAULT_MAX_TOKENS)
        if client.reasoning.get("mode") == "enabled":
            kwargs.pop("temperature", None)
    return kwargs


def _request(client, kwargs):
    from lib.llm_client import _is_parameter_unsupported, _is_temperature_unsupported
    create = (client.client.chat.completions.create if client.api_format == "chat" else
              client.client.responses.create if client.api_format == "responses" else client.client.messages.create)
    for _ in range(3):
        try:
            return client._create(create, kwargs)
        except Exception as error:
            if any(_is_parameter_unsupported(error, key) for key in ("tools", "tool_choice")):
                rejection = ValueError("model_tools_unsupported")
                rejection.definite_rejection = rejected_before_generation(error)
                raise rejection from None
            if client.reasoning.get("mode") != "auto" and any(_is_parameter_unsupported(error, key) for key in
                    ("thinking", "reasoning_effort", "reasoning.effort", "output_config", "budget_tokens")):
                rejection = ValueError("model_reasoning_parameter_unsupported")
                rejection.definite_rejection = rejected_before_generation(error)
                raise rejection from None
            if "temperature" in kwargs and _is_temperature_unsupported(error):
                kwargs.pop("temperature")
            elif "max_tokens" in kwargs and client.api_format == "chat" and _is_parameter_unsupported(error, "max_tokens"):
                kwargs["max_completion_tokens"] = kwargs.pop("max_tokens")
            else:
                raise
    raise ValueError("model_tool_parameter_negotiation_failed")


def _assistant(response, api, names):
    if api == "chat":
        choices = _get(response, "choices", [])
        if len(choices) != 1:
            raise ValueError("invalid_model_tool_response")
        choice, message = choices[0], _get(choices[0], "message")
        raw = _plain(message)
        if not isinstance(raw, dict) or not isinstance(raw.get("content", "") or "", str):
            raise ValueError("invalid_model_tool_response")
        result = {"role": "assistant", "content": raw.get("content") or "", "tool_calls": raw.get("tool_calls") or []}
        _calls(result)
        if _get(choice, "finish_reason") != ("tool_calls" if result["tool_calls"] else "stop"):
            raise ModelStreamError("model_stream_incomplete")
        if "reasoning_content" in raw and raw["reasoning_content"] is not None:
            if not isinstance(raw["reasoning_content"], str):
                raise ValueError("invalid_model_tool_reasoning")
            result["reasoning_content"] = raw["reasoning_content"]
        if "reasoning_details" in raw:
            result["provider_state"] = {"api_format": api, "reasoning_details": raw["reasoning_details"]}
    else:
        if (api == "responses" and _get(response, "status") != "completed"):
            raise ModelStreamError("model_stream_incomplete")
        blocks = _plain(_get(response, "output" if api == "responses" else "content", []))
        result = _block_assistant(blocks, api)
        if api == "anthropic" and _get(response, "stop_reason") not in (
                {"tool_use"} if result["tool_calls"] else {"end_turn", "stop_sequence"}):
            raise ModelStreamError("model_stream_incomplete")
        result["provider_state"] = {"api_format": api,
            "output_items" if api == "responses" else "content_blocks": blocks}
        reasoning = extract_response_details(response, api).get("reasoning_content")
        if reasoning:
            result["reasoning_content"] = reasoning
    identities = [call["id"] for call in result["tool_calls"]]
    if len(set(identities)) != len(identities):
        raise ValueError("duplicate_model_tool_call")
    if any(call["function"]["name"] not in names for call in result["tool_calls"]):
        raise ValueError("model_unexpected_tool_call")
    if not result["tool_calls"] and not result["content"].strip():
        raise ValueError("model_visible_output_missing")
    _json(result)
    return result


def tool_turn(client, messages, tools, *, max_tokens=1024, temperature=0.7, retries=3,
              response_details=None, on_stream=None):
    """Return a complete assistant/tool decision; never execute a function.

    SDK requests are nonstreaming. After the entire response arrives, the
    observer receives each complete text channel once, then completion. These
    events carry ``delivery=complete_response`` and do not simulate token timing.
    """
    from lib.llm_client import BudgetExceeded
    from lib.model_request_scheduler import ModelQueueTimeout
    if response_details is not None:
        response_details.clear()
    if max_tokens is not None and (type(max_tokens) is not int or max_tokens <= 0):
        raise ValueError("max_tokens must be a positive integer")
    if type(retries) is not int or not 1 <= retries <= 10:
        raise ValueError("invalid_model_request_attempts")
    if type(temperature) not in (int, float) or not math.isfinite(temperature):
        raise ValueError("invalid_model_temperature")
    tools = _tools(tools)
    messages = _history(messages, client.api_format)
    if client.model_info.get("tools") is False:
        raise ValueError("model_tools_unsupported")
    if urlsplit(client.base_url).hostname == "api.deepseek.com" and urlsplit(client.base_url).path.rstrip("/") == "/beta":
        raise ValueError("model_tool_turn_native_prefill_unsupported")
    # Legacy chat preflight only counts reasoning used as a beta prefix. This
    # tool channel must additionally charge every historical thought, signature,
    # encrypted item and complete tool schema. Overcount duplicates conservatively.
    accounting = tool_context_accounting(messages, tools)
    request_max, reservation_usd = client._budget_request_bound(accounting, max_tokens)
    kwargs = _request_kwargs(client, messages, tools, request_max, temperature)
    names = {tool["function"]["name"] for tool in tools}
    callback_error = cancel_error = None

    def check_cancel():
        nonlocal cancel_error
        if client.cancel_check:
            try:
                client.cancel_check()
            except BaseException as error:
                cancel_error = error
                raise

    def notify(event):
        nonlocal callback_error
        if on_stream:
            try:
                on_stream(event)
            except BaseException as error:
                callback_error = error
                raise

    failure = {"kind": "invalid", "code": "model_output_invalid"}
    for attempt in range(retries):
        reservations, admission = [], ExitStack()
        request_started = definite_rejection = False
        delay = None
        try:
            check_cancel()
            if client.scheduler is not None:
                admission.enter_context(client.scheduler.acquire(cancel_check=check_cancel,
                    on_wait=(lambda info: notify({"type": "queued", **info})) if on_stream else None))
            else:
                SERVICE_COOLDOWNS.wait(client._service_identity,
                                      **({"cancel_check": check_cancel} if client.cancel_check else {}))
            check_cancel()
            for guard in client._budgets():
                if reservation_usd:
                    reservations.append((guard, guard.reserve(reservation_usd)))
                else:
                    guard.check()
            notify({"type": "start", "api_format": client.api_format})
            request_started = True
            response = _request(client, kwargs)
            usage = _get(response, "usage")
            prompt = _get(usage, "prompt_tokens" if client.api_format == "chat" else "input_tokens", 0) or 0
            output = _get(usage, "completion_tokens" if client.api_format == "chat" else "output_tokens", 0) or 0
            if client.api_format == "anthropic":
                prompt += (_get(usage, "cache_creation_input_tokens", 0) or 0) + (_get(usage, "cache_read_input_tokens", 0) or 0)
            client.usage["calls"] += 1
            client.usage["prompt_tokens"] += prompt
            client.usage["completion_tokens"] += output
            if client._budgets() and (client.price_input or client.price_output):
                cost = (prompt * client.price_input + output * client.price_output) / 1e6
                if reservations:
                    client._settle_all(reservations, cost if prompt or output else reservation_usd)
                else:
                    first_error = None
                    for guard in client._budgets():
                        try:
                            guard.add_usd(cost)
                        except Exception as error:
                            if first_error is None:
                                first_error = error
                    if first_error is not None:
                        raise first_error
            # A provider response incurs usage even if its calls are truncated,
            # malformed or forbidden. Validate before publishing any content.
            result = _assistant(response, client.api_format, names)
            check_cancel()
            details = extract_response_details(response, client.api_format)
            reasoning = "".join(details.get(key, "") for key in ("reasoning_content", "reasoning_summary"))
            for channel, text in (("reasoning", reasoning), ("text", result["content"])):
                if text:
                    notify({"type": "delta", "channel": channel, "text": text,
                            "delivery": "complete_response"})
            notify({"type": "response_completed", "prompt_tokens": prompt, "completion_tokens": output,
                    "content": result["content"], "tool_calls": len(result["tool_calls"]),
                    "delivery": "complete_response"})
            commit_response_details(response_details, details)
            return result
        except BudgetExceeded:
            raise
        except ValueError as error:
            definite_rejection = getattr(error, "definite_rejection", False)
            raise
        except Exception as error:
            if error is callback_error or error is cancel_error:
                raise
            if isinstance(error, ModelQueueTimeout):
                raise ModelRequestError(error.code, error.kind, attempt) from None
            definite_rejection = rejected_before_generation(error)
            failure = classify_request_error(error)
            if failure["kind"] == "transient":
                delay = retry_delay(error, attempt)
                if failure["code"] in {"service_rate_limited", "service_unavailable"}:
                    if client.scheduler is not None:
                        client.scheduler.defer(delay)
                    else:
                        SERVICE_COOLDOWNS.defer(client._service_identity, delay)
        finally:
            try:
                if reservations:
                    client._settle_all(reservations,
                        reservation_usd if request_started and not definite_rejection else 0.0)
            finally:
                admission.close()
        if failure["kind"] != "transient" or attempt + 1 >= retries:
            break
        if failure["code"] not in {"service_rate_limited", "service_unavailable"}:
            if client.cancel_check:
                cancelable_wait(delay, check_cancel)
            else:
                time.sleep(delay)
    raise ModelRequestError(failure["code"], failure["kind"], attempt + 1) from None
