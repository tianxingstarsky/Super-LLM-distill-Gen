"""Versioned normalized conversation import contract, independent of agent vendors."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

MAX_IMPORT_BYTES = 50 * 1024 * 1024
COUNT_FIELDS = ("records", "messages", "assistant_messages", "reasoning_messages", "tool_calls", "tool_results")


def import_digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def validate_import_name(name: str) -> None:
    if (not isinstance(name, str) or not name or len(name) > 240
            or name in {".", ".."} or name.endswith((".", " "))
            or re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", name, re.I)
            or re.search(r'[<>:"/\\|?*\x00-\x1f\x7f\ud800-\udfff]', name)):
        raise ValueError("invalid_conversation_import_name")
    if len(name.encode("utf-8")) > 255:
        raise ValueError("invalid_conversation_import_name")


def validate_import_record(record: dict) -> dict:
    """Validate source structure without grading, rewriting or inventing reasoning."""
    if not isinstance(record, dict) or not isinstance(record.get("id"), str) or not record["id"]:
        raise ValueError("conversation_import_invalid_record")
    messages = record.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("conversation_import_missing_messages")
    if not isinstance(record.get("tools", []), list):
        raise ValueError("conversation_import_invalid_tools")
    pending, seen = set(), set()
    counts = dict.fromkeys(COUNT_FIELDS, 0)
    counts["records"] = 1
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"system", "developer", "user", "assistant", "tool"}:
            raise ValueError("conversation_import_invalid_role")
        role = message["role"]
        content = message.get("content", "")
        if not isinstance(content, str):
            raise ValueError("conversation_import_unsupported_content")
        if "reasoning_content" in message and not isinstance(message["reasoning_content"], str):
            raise ValueError("conversation_import_invalid_reasoning")
        calls = message.get("tool_calls", [])
        if not isinstance(calls, list) or (calls and role != "assistant"):
            raise ValueError("conversation_import_invalid_tool_calls")
        if role == "tool":
            call_id = message.get("tool_call_id")
            if not isinstance(call_id, str) or call_id not in pending:
                raise ValueError("conversation_import_orphan_tool_result")
            pending.remove(call_id)
            counts["tool_results"] += 1
        elif pending:
            raise ValueError("conversation_import_missing_tool_result")
        for call in calls:
            if (not isinstance(call, dict) or not isinstance(call.get("id"), str)
                    or not call["id"] or call["id"] in seen):
                raise ValueError("conversation_import_invalid_tool_id")
            if call.get("type") != "function":
                raise ValueError("conversation_import_invalid_tool_function")
            function = call.get("function")
            if not isinstance(function, dict) or not isinstance(function.get("name"), str) or not function["name"]:
                raise ValueError("conversation_import_invalid_tool_function")
            arguments = function.get("arguments")
            try:
                if not isinstance(arguments, str) or not isinstance(json.loads(arguments), dict):
                    raise ValueError()
            except (ValueError, TypeError):
                raise ValueError("conversation_import_invalid_tool_arguments") from None
            pending.add(call["id"])
            seen.add(call["id"])
            counts["tool_calls"] += 1
        for flag in ("is_error", "isError"):
            if flag in message and type(message[flag]) is not bool:
                raise ValueError("conversation_import_invalid_tool_error_flag")
        counts["messages"] += 1
        counts["assistant_messages"] += int(role == "assistant")
        counts["reasoning_messages"] += int(bool(message.get("reasoning_content")))
    if pending:
        raise ValueError("conversation_import_missing_tool_result")
    return counts


def iter_import_records(content: bytes):
    if not isinstance(content, bytes) or not content or len(content) > MAX_IMPORT_BYTES:
        raise ValueError("conversation_import_invalid_output_size")
    try:
        text = content.decode("utf-8")
    except UnicodeError:
        raise ValueError("conversation_import_invalid_encoding") from None
    seen = set()
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError:
            raise ValueError("conversation_import_invalid_jsonl") from None
        validate_import_record(record)
        if record["id"] in seen:
            raise ValueError("conversation_import_duplicate_id")
        seen.add(record["id"])
        yield record
    if not seen:
        raise ValueError("conversation_import_empty_output")


def preview_record(record: dict) -> dict:
    preview = deepcopy(record)
    truncated = len(preview["messages"]) > 50
    preview["messages"] = preview["messages"][:50]

    def bound(value):
        nonlocal truncated
        if isinstance(value, str) and len(value) > 4000:
            truncated = True
            return value[:4000] + "…"
        if isinstance(value, dict):
            return {key: bound(item) for key, item in value.items()}
        if isinstance(value, list):
            if len(value) > 50:
                truncated = True
            return [bound(item) for item in value[:50]]
        return value

    preview = bound(preview)
    preview["preview_truncated"] = truncated
    preview["training_issues"] = training_issues(record)
    return preview


def training_issues(record: dict) -> dict:
    """Import authenticity is distinct from existing downstream training eligibility."""
    from lib.domain.source_conversation import source_conversation_issue
    return {target: source_conversation_issue(record, [target]) for target in ("sft", "agent")}


def import_eligibility(content: bytes) -> dict:
    eligibility = {target: {"eligible_records": 0, "blocked_records": 0, "reasons": {},
                            "eligible_ids": [], "eligible_ids_truncated": False}
                   for target in ("sft", "agent")}
    for record in iter_import_records(content):
        for target, issue in training_issues(record).items():
            entry = eligibility[target]
            entry["blocked_records" if issue else "eligible_records"] += 1
            if issue:
                entry["reasons"][issue] = entry["reasons"].get(issue, 0) + 1
            elif len(entry["eligible_ids"]) < 10000:
                entry["eligible_ids"].append(record["id"])
            else:
                entry["eligible_ids_truncated"] = True
    return eligibility


def inspect_import(content: bytes, report: dict, source_sha256: str, preview_limit: int = 3) -> tuple[dict, list[dict]]:
    if type(preview_limit) is not int or not 1 <= preview_limit <= 5:
        raise ValueError("conversation_import_invalid_preview_limit")
    if (not isinstance(report, dict) or report.get("protocol") != "dataforge.conversation_import"
            or type(report.get("version")) is not int or report["version"] != 1 or report.get("status") != "ready"
            or report.get("source_sha256") != source_sha256
            or report.get("output_sha256") != import_digest(content)
            or report.get("output_bytes") != len(content)):
        raise ValueError("conversation_import_receipt_mismatch")
    counts = dict.fromkeys(COUNT_FIELDS, 0)
    previews = []
    for record in iter_import_records(content):
        for key, count in validate_import_record(record).items():
            counts[key] += count
        if len(previews) < preview_limit:
            previews.append(preview_record(record))
    if any(type(report.get(key)) is not int or report[key] != count for key, count in counts.items()):
        raise ValueError("conversation_import_count_mismatch")
    return counts, previews
