"""Recorded conversation boundaries, independent of storage and interface rendering."""
from __future__ import annotations

import json
from collections.abc import Iterator


def tool_result_user(message: dict) -> bool:
    """Anthropic tool results are carried in a user-role content block."""
    return (message.get("role") == "user" and isinstance(message.get("content"), list)
            and bool(message["content"])
            and all(isinstance(block, dict) and block.get("type") == "tool_result"
                    for block in message["content"]))


def tool_calls(message: dict) -> list[dict]:
    calls = message.get("toolCalls") or message.get("tool_calls") or []
    if isinstance(calls, str):
        try:
            calls = json.loads(calls)
        except ValueError:
            calls = []
    if isinstance(calls, dict):
        calls = [calls]
    result = [call for call in calls if isinstance(call, dict)] if isinstance(calls, list) else []
    content = message.get("content")
    if isinstance(content, list):
        result.extend(block for block in content
                      if isinstance(block, dict) and block.get("type") in {"tool_use", "tool_call"})
    return result


def tool_call_names(message: dict) -> list[str]:
    names = []
    for call in tool_calls(message):
        function = call.get("function") if isinstance(call.get("function"), dict) else call
        name = function.get("name") or call.get("toolName")
        if name:
            names.append(str(name))
    return names


def tool_call_ids(message: dict) -> list[str]:
    return [str(call_id) for call in tool_calls(message)
            if (call_id := call.get("id") or call.get("toolCallId") or call.get("tool_call_id"))]


def tool_result_ids(message: dict) -> list[str]:
    if message.get("role") == "tool":
        call_id = message.get("tool_call_id") or message.get("toolCallId")
        return [str(call_id)] if call_id else []
    content = message.get("content")
    if message.get("role") == "user" and isinstance(content, list) and content:
        # Mixed text/result messages remain separate so user input stays visible.
        if all(isinstance(block, dict) and block.get("type") == "tool_result"
               and block.get("tool_use_id") for block in content):
            return [str(block["tool_use_id"]) for block in content]
    return []


def trace_ranges(messages: list[dict]) -> Iterator[tuple[int, int]]:
    """Pair only adjacent, unique results belonging to the active call group."""
    index = 0
    while index < len(messages):
        end = index + 1
        message = messages[index]
        ids = tool_call_ids(message) if message.get("role") == "assistant" and tool_call_names(message) else []
        returned: set[str] = set()
        while ids and end < len(messages):
            result_ids = tool_result_ids(messages[end])
            if (not result_ids or len(result_ids) != len(set(result_ids))
                    or not set(result_ids).issubset(ids) or returned.intersection(result_ids)):
                break
            returned.update(result_ids)
            end += 1
        yield index, end
        index = end


def dialogue_ranges(messages: list[dict]) -> Iterator[tuple[int, int]]:
    """Keep initial context separate and mixed result/input messages as user turns."""
    if not messages:
        return
    start = 0
    for index, message in enumerate(messages):
        if index and message.get("role") == "user" and not tool_result_user(message):
            yield start, index
            start = index
    yield start, len(messages)


def message_windows(target: str, messages: list[dict], size: int = 8) -> list[tuple[int, int, int]]:
    if type(size) is not int or size < 1:
        raise ValueError("invalid_preview_window_size")
    groups = trace_ranges(messages) if target in {"agent", "agent_negative"} else dialogue_ranges(messages)
    windows = []
    start = end = offset = count = 0
    for index, (first, end) in enumerate(groups):
        if count == 0:
            start, offset = first, index
        count += 1
        if count == size:
            windows.append((start, end, offset))
            count = 0
    if count:
        windows.append((start, end, offset))
    return windows


