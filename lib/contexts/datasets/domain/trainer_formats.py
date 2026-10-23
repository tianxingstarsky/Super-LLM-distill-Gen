"""Pure, strict conversion from internal records to TRL conversational datasets.

Contracts: https://huggingface.co/docs/trl/dataset_formats (Tool Calling and
Preference sections) and https://huggingface.co/docs/trl/orpo_trainer.
No chat template is applied here. The selected tokenizer must support tool
calling and, if present, the ``thinking`` field used by Harmony-style models.
"""
from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any


_ROLES = frozenset({"system", "developer", "user", "assistant", "tool"})
_MEDIA_KEYS = ("images", "image", "image_url", "audio", "audio_url", "video", "video_url",
               "files", "file", "file_data", "inline_data", "attachments")


def _has_media(record: Mapping[str, Any]) -> bool:
    return any(bool(record.get(key)) for key in _MEDIA_KEYS)


def _json_copy(value: Any, error: str) -> Any:
    """Copy JSON data and reject unsupported objects and non-finite numbers."""
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ValueError(error) from exc


def _nonempty_text(value: Any, error: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(error)
    return value


def _tool_schemas(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("trl_invalid_tools")
    converted = []
    names = set()
    for entry in value:
        if not isinstance(entry, Mapping):
            raise ValueError("trl_invalid_tool_schema")
        if entry.get("type") not in (None, "function"):
            raise ValueError("trl_invalid_tool_schema")
        function = entry.get("function") if "function" in entry else entry
        if not isinstance(function, Mapping):
            raise ValueError("trl_invalid_tool_schema")
        name = _nonempty_text(function.get("name"), "trl_invalid_tool_name")
        if name in names:
            raise ValueError("trl_duplicate_tool_name")
        names.add(name)
        parameters = function.get("parameters", function.get("input_schema"))
        if parameters is None:
            # A declared no-argument function is still a valid JSON schema.
            # Its closed object rejects calls carrying undeclared arguments.
            parameters = {"type": "object", "properties": {}, "required": [],
                          "additionalProperties": False}
        if not isinstance(parameters, Mapping) or parameters.get("type") != "object":
            raise ValueError("trl_invalid_tool_parameters")
        properties = parameters.get("properties", {})
        required = parameters.get("required", [])
        if not isinstance(properties, Mapping) or not isinstance(required, list):
            raise ValueError("trl_invalid_tool_parameters")
        if any(not isinstance(key, str) or not key for key in properties):
            raise ValueError("trl_invalid_tool_parameters")
        if any(not isinstance(key, str) or key not in properties for key in required):
            raise ValueError("trl_invalid_tool_parameters")
        description = function.get("description", "")
        if not isinstance(description, str):
            raise ValueError("trl_invalid_tool_description")
        normalized_parameters = _json_copy(dict(parameters), "trl_invalid_tool_parameters")
        normalized_parameters["required"] = sorted(set(required))
        normalized_parameters.setdefault("properties", {})
        output_function: dict[str, Any] = {
            "name": name,
            "description": description,
            "parameters": normalized_parameters,
        }
        if "return" in function:
            output_function["return"] = _json_copy(function["return"], "trl_invalid_tool_return")
        converted.append({"type": "function", "function": output_function})
    return sorted(converted, key=lambda tool: tool["function"]["name"])


def _calls(value: Any) -> list[Mapping[str, Any]]:
    if value is None or value == []:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("trl_invalid_tool_calls") from exc
    if isinstance(value, Mapping):
        value = [value]
    if not isinstance(value, list) or any(not isinstance(call, Mapping) for call in value):
        raise ValueError("trl_invalid_tool_calls")
    return value


def _call_data(call: Mapping[str, Any]) -> tuple[str | None, str, dict[str, Any]]:
    function = call.get("function", call)
    if not isinstance(function, Mapping):
        raise ValueError("trl_invalid_tool_call")
    name = _nonempty_text(function.get("name", call.get("toolName")), "trl_missing_tool_name")
    call_id = call.get("id", call.get("toolCallId", call.get("tool_call_id")))
    if call_id is not None and (not isinstance(call_id, str) or not call_id.strip()):
        raise ValueError("trl_invalid_tool_call_id")
    arguments = function.get("arguments", function.get("input", function.get("args", {})))
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (TypeError, ValueError) as exc:
            raise ValueError("trl_invalid_tool_arguments") from exc
    if not isinstance(arguments, Mapping):
        raise ValueError("trl_invalid_tool_arguments")
    return call_id, name, _json_copy(dict(arguments), "trl_invalid_tool_arguments")


def _convert_messages(messages: Any, tools: list[dict[str, Any]], *, require_user: bool,
                      require_final_assistant: bool) -> list[dict[str, Any]]:
    if not isinstance(messages, list) or not messages:
        raise ValueError("trl_messages_required")
    known_tools = {entry["function"]["name"]: entry["function"]["parameters"] for entry in tools}
    converted: list[dict[str, Any]] = []
    pending: list[tuple[str | None, str]] = []
    seen_ids: set[str] = set()
    has_user = False
    for message in messages:
        if not isinstance(message, Mapping):
            raise ValueError("trl_invalid_message")
        role = message.get("role")
        if role not in _ROLES:
            raise ValueError("trl_invalid_role")
        content = message.get("content", "")
        if content is None and role == "assistant":
            content = ""
        if not isinstance(content, str):
            raise ValueError("trl_multimodal_content_unsupported")
        if _has_media(message):
            raise ValueError("trl_multimodal_content_unsupported")
        if message.get("toolCalls") and message.get("tool_calls"):
            raise ValueError("trl_ambiguous_tool_calls")
        call_value = message.get("toolCalls") or message.get("tool_calls")
        calls = _calls(call_value)
        if calls and role != "assistant":
            raise ValueError("trl_invalid_tool_calls")

        if role == "tool":
            if not pending:
                raise ValueError("trl_orphan_tool_result")
            result_id = message.get("toolCallId", message.get("tool_call_id"))
            result_name = message.get("toolName", message.get("tool_name", message.get("name")))
            if result_id is not None and (not isinstance(result_id, str) or not result_id):
                raise ValueError("trl_invalid_tool_call_id")
            if result_name is not None and (not isinstance(result_name, str) or not result_name):
                raise ValueError("trl_invalid_tool_name")
            matches = [index for index, (call_id, name) in enumerate(pending)
                       if (call_id == result_id if result_id is not None else
                           name == result_name if result_name is not None else False)]
            if len(matches) != 1:
                raise ValueError("trl_orphan_tool_result")
            call_id, name = pending.pop(matches[0])
            if result_name is not None and result_name != name:
                raise ValueError("trl_tool_result_name_mismatch")
            output: dict[str, Any] = {"role": "tool", "name": name, "content": content}
            if call_id is not None:
                output["tool_call_id"] = call_id
            if message.get("isError") or message.get("is_error"):
                output["is_error"] = True
            converted.append(output)
            continue

        if pending:
            raise ValueError("trl_unclosed_tool_call")
        if role == "user":
            has_user = True
        if role != "assistant":
            _nonempty_text(content, "trl_empty_message")
        output = {"role": role}
        if content or not calls:
            output["content"] = content
        if role == "assistant":
            reasoning_values = [message[key] for key in ("reasoning_content", "reasoning", "thinking")
                                if key in message and message[key] not in (None, "")]
            if reasoning_values:
                if any(value != reasoning_values[0] for value in reasoning_values[1:]):
                    raise ValueError("trl_conflicting_reasoning_fields")
                output["thinking"] = _nonempty_text(reasoning_values[0], "trl_invalid_reasoning")
            if not content.strip() and not calls:
                raise ValueError("trl_empty_assistant_message")
            if calls:
                normalized_calls = []
                for call in calls:
                    call_id, name, arguments = _call_data(call)
                    if name not in known_tools:
                        raise ValueError("trl_undefined_tool")
                    schema = known_tools[name]
                    if schema.get("additionalProperties") is False and any(
                        key not in schema["properties"] for key in arguments
                    ):
                        raise ValueError("trl_tool_arguments_outside_schema")
                    if call_id is not None:
                        if call_id in seen_ids:
                            raise ValueError("trl_duplicate_tool_call_id")
                        seen_ids.add(call_id)
                    function_call: dict[str, Any] = {"type": "function", "function": {
                        "name": name, "arguments": arguments,
                    }}
                    if call_id is not None:
                        function_call["id"] = call_id
                    normalized_calls.append(function_call)
                    pending.append((call_id, name))
                output["tool_calls"] = normalized_calls
        converted.append(output)
    if pending:
        raise ValueError("trl_unclosed_tool_call")
    if require_user and not has_user:
        raise ValueError("trl_user_message_required")
    if require_final_assistant and (converted[-1]["role"] != "assistant"
                                    or not converted[-1].get("content", "").strip()):
        raise ValueError("trl_final_assistant_answer_required")
    return converted


def to_trl_sft(sample: Mapping[str, Any]) -> dict[str, Any]:
    """Convert a complete text SFT/Agent record to TRL ``messages`` + ``tools``."""
    if not isinstance(sample, Mapping):
        raise ValueError("trl_sample_must_be_object")
    if _has_media(sample):
        raise ValueError("trl_multimodal_content_unsupported")
    tools = _tool_schemas(sample.get("tools"))
    messages = _convert_messages(sample.get("messages"), tools, require_user=True,
                                 require_final_assistant=True)
    output: dict[str, Any] = {"messages": messages}
    if tools:
        output["tools"] = tools
    return output


def to_trl_preference(pair: Mapping[str, Any]) -> dict[str, Any]:
    """Convert DPO/ORPO candidates to an explicit-prompt TRL preference row."""
    if not isinstance(pair, Mapping):
        raise ValueError("trl_preference_must_be_object")
    if _has_media(pair):
        raise ValueError("trl_multimodal_content_unsupported")
    prompt, chosen, rejected = (pair.get(key) for key in ("prompt", "chosen", "rejected"))
    if all(isinstance(value, str) for value in (prompt, chosen, rejected)):
        values = {key: _nonempty_text(value, f"trl_preference_{key}_required")
                  for key, value in (("prompt", prompt), ("chosen", chosen), ("rejected", rejected))}
        if values["chosen"].strip() == values["rejected"].strip():
            raise ValueError("trl_preference_answers_must_differ")
        if pair.get("tools"):
            raise ValueError("trl_tools_require_conversation")
        return values
    if not isinstance(prompt, list) or not prompt:
        raise ValueError("trl_preference_prompt_required")
    tools = _tool_schemas(pair.get("tools"))
    prompt_messages = _convert_messages(prompt, tools, require_user=True, require_final_assistant=False)
    choices = []
    for field, value in (("chosen", chosen), ("rejected", rejected)):
        if isinstance(value, str):
            value = [{"role": "assistant", "content": value}]
        messages = _convert_messages(value, tools, require_user=False, require_final_assistant=True)
        if messages[0]["role"] != "assistant":
            raise ValueError(f"trl_preference_{field}_must_start_with_assistant")
        if messages[:len(prompt_messages)] == prompt_messages:
            raise ValueError("trl_preference_prompt_repeated")
        choices.append(messages)
    if choices[0][-1]["content"].strip() == choices[1][-1]["content"].strip():
        raise ValueError("trl_preference_answers_must_differ")
    output = {"prompt": prompt_messages, "chosen": choices[0], "rejected": choices[1]}
    if tools:
        output["tools"] = tools
    return output
