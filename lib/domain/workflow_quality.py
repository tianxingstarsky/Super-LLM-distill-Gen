"""Strict, deterministic contracts shared by automatic workflow stages."""
from __future__ import annotations

import json
import re


POLICY = "training-workflow-v2"
SECRET = re.compile(r"(?:sk-[A-Za-z0-9_-]{16,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|(?:api[_-]?key|password|密码|密钥)[\"']?\s*[:=]\s*[\"']?[^\s\"']{8,})", re.I)
PERSONAL_DATA = re.compile(
    r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)|"
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|"
    r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[0-9Xx](?!\d)"
)


def text_issue(text):
    if not isinstance(text, str) or not text.strip():
        return "empty_text"
    if "\ufffd" in text or "\x00" in text:
        return "invalid_encoding"
    if SECRET.search(text):
        return "potential_secret"
    if PERSONAL_DATA.search(text):
        return "potential_personal_data"
    return None


def conversation_issue(messages, final=True):
    if not isinstance(messages, list) or not messages:
        return "missing_messages"
    pending, seen = set(), set()
    has_user = False
    for message in messages:
        if not isinstance(message, dict):
            return "invalid_message"
        role = message.get("role")
        if role not in {"system", "user", "assistant", "tool"}:
            return "invalid_role"
        if message.get("isError"):
            return "unresolved_tool_error"
        content = message.get("content", "")
        if not isinstance(content, str):
            return "multimodal_requires_dedicated_pipeline"
        issue = text_issue(content) if content else None
        if issue:
            return issue
        reasoning = message.get("reasoning_content", "")
        if reasoning and text_issue(reasoning):
            return text_issue(reasoning)
        calls = message.get("tool_calls", message.get("toolCalls", []))
        if SECRET.search(canonical(message)):
            return "potential_secret"
        if calls and (role != "assistant" or not isinstance(calls, list)):
            return "invalid_tool_calls"
        if role == "tool":
            call_id = message.get("tool_call_id", message.get("toolCallId"))
            if call_id not in pending:
                return "orphan_tool_result"
            pending.remove(call_id)
        elif pending:
            return "missing_tool_result"
        for call in calls or []:
            if not isinstance(call, dict) or not isinstance(call.get("id"), str) or not call["id"] or call["id"] in seen:
                return "invalid_tool_id"
            function = call.get("function") or call
            if not isinstance(function, dict) or not function.get("name"):
                return "missing_tool_name"
            arguments = function.get("arguments", function.get("input", {}))
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except ValueError:
                    return "invalid_tool_arguments"
            if not isinstance(arguments, dict):
                return "invalid_tool_arguments"
            pending.add(call["id"])
            seen.add(call["id"])
        if role == "user":
            has_user = True
        if not content.strip() and not calls:
            return "empty_message"
    if pending:
        return "missing_tool_result"
    if not has_user:
        return "missing_user"
    if final and (messages[-1].get("role") != "assistant" or not messages[-1].get("content", "").strip()):
        return "missing_final_answer"
    return None


def verdict(value):
    """Invalid judge output is an execution failure, never an implicit low score."""
    if not isinstance(value, dict):
        raise ValueError("invalid_judge_schema")
    if any(type(value.get(key)) is not bool for key in ("keep", "grounded", "reasoning_valid")):
        raise ValueError("invalid_judge_boolean")
    if type(value.get("correctness")) is not int or not 1 <= value["correctness"] <= 5:
        raise ValueError("invalid_judge_score")
    if not isinstance(value.get("reason"), str) or not value["reason"].strip():
        raise ValueError("missing_judge_reason")
    scores = value.get("scores")
    required = {"correctness", "reasoning", "grounding", "instruction", "safety"}
    if not isinstance(scores, dict) or set(scores) != required or any(type(scores[k]) is not int or not 1 <= scores[k] <= 5 for k in required):
        raise ValueError("invalid_jev_dimensions")
    return value


def accepted(check):
    return (check["keep"] and check["grounded"] and check["reasoning_valid"]
            and check["correctness"] >= 4 and all(v >= 4 for v in check["scores"].values()))


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def same_answer(a, b):
    return re.sub(r"\s+", "", a) == re.sub(r"\s+", "", b)
