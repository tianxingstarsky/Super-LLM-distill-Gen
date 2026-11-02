"""Atomic decontextualization edits; neither factual repair nor quality approval."""
from copy import deepcopy
from hashlib import sha256
import json
import re

from jsonschema import Draft202012Validator

from .authored_submission import strict_tool_schema
from .dialogue import dialogue_digest
from .native_feedback import native_response_issue

EDIT_KNOWLEDGE_REASONING = "edit_knowledge_reasoning"
EDIT_KNOWLEDGE_REASONING_SEGMENTS = "edit_knowledge_reasoning_segments"


def _schema():
    def closed(properties):
        return {"type": "object", "properties": properties, "required": list(properties),
                "additionalProperties": False}

    return closed({
        "status": {"type": "string", "enum": ["unchanged", "edited", "needs_content_repair"]},
        "edits": {"type": "array", "items": closed({
            "old_text": {"type": "string", "minLength": 1}, "new_text": {"type": "string"}})},
        "issues": {"type": "array", "items": closed({
            "quote": {"type": "string"}, "reason": {"type": "string"}})},
    })


def knowledge_reasoning_editor_tool():
    return {"type": "function", "function": {"name": EDIT_KNOWLEDGE_REASONING, "strict": True,
        "description": "Submit local edits that make the supplied reasoning self-contained while preserving "
            "its facts, conditions, attribution and uncertainty. Report substantive problems separately. "
            "The program applies literal edits atomically; this is not a quality verdict.",
        "parameters": strict_tool_schema(_schema())}}


def _text(value, *, nonblank=False):
    if not isinstance(value, str) or "\x00" in value or nonblank and not value.strip():
        raise ValueError("knowledge_reasoning_editor_invalid_text")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("knowledge_reasoning_editor_invalid_text") from None
    return value


def _unique(pairs):
    value = {}
    for key, child in pairs:
        if key in value:
            raise ValueError("knowledge_reasoning_editor_duplicate_field")
        value[key] = child
    return value


def _submission(assistant, tool_name, schema):
    if not isinstance(assistant, dict) or assistant.get("role") != "assistant":
        raise ValueError("knowledge_reasoning_editor_invalid_submission")
    content = assistant.get("content")
    calls = assistant.get("tool_calls")
    if ((content is not None and (not isinstance(content, str) or content.strip()))
            or not isinstance(calls, list) or len(calls) != 1
            or not isinstance(calls[0], dict) or not isinstance(calls[0].get("function"), dict)
            or calls[0]["function"].get("name") != tool_name
            or not isinstance(calls[0]["function"].get("arguments"), str)):
        raise ValueError("knowledge_reasoning_editor_invalid_submission")
    try:
        patch = json.loads(calls[0]["function"]["arguments"], object_pairs_hook=_unique)
    except json.JSONDecodeError:
        raise ValueError("knowledge_reasoning_editor_invalid_json") from None
    if not Draft202012Validator(schema).is_valid(patch):
        raise ValueError("knowledge_reasoning_editor_invalid_schema")
    status, edits, issues = patch["status"], patch["edits"], patch["issues"]
    if status == "unchanged" and edits or status == "edited" and not edits:
        raise ValueError("knowledge_reasoning_editor_status_mismatch")
    return patch


def _check_issues(issues, original, final_answer):
    for issue in issues:
        quote = _text(issue["quote"])
        _text(issue["reason"], nonblank=True)
        if quote and quote not in original and (final_answer is None or quote not in final_answer):
            raise ValueError("knowledge_reasoning_editor_unbound_issue")


def _receipt(original, derived, patch, final_answer, *, version=1):
    if not derived.strip():
        raise ValueError("knowledge_reasoning_editor_empty_reasoning")
    if patch["edits"] and derived == original:
        raise ValueError("knowledge_reasoning_editor_unchanged_edit")
    receipt = {"version": version, "status": patch["status"], "edits": deepcopy(patch["edits"]),
        "issues": deepcopy(patch["issues"]),
        "original_sha256": sha256(original.encode("utf-8")).hexdigest(),
        "derived_sha256": sha256(derived.encode("utf-8")).hexdigest(), "text_hash_scope": "raw_utf8",
        "changed": derived != original, "content_repair_required": patch["status"] == "needs_content_repair",
        "semantic_status": "not_assessed", "application": "atomic_replacements_in_original"}
    if final_answer is not None:
        receipt["fixed_answer_sha256"] = sha256(final_answer.encode("utf-8")).hexdigest()
    return derived, {**receipt, "sha256": dialogue_digest(receipt)}


def apply_edits(original, assistant, *, final_answer=None):
    """Apply disjoint replacements against one immutable original, or fail whole."""
    _text(original, nonblank=True)
    if final_answer is not None:
        _text(final_answer, nonblank=True)
    patch = _submission(assistant, EDIT_KNOWLEDGE_REASONING, _schema())
    edits = patch["edits"]
    _check_issues(patch["issues"], original, final_answer)
    spans = []
    for edit in edits:
        old, new = _text(edit["old_text"]), _text(edit["new_text"])
        if old == new:
            raise ValueError("knowledge_reasoning_editor_unchanged_edit")
        start = original.find(old)
        if start < 0:
            raise ValueError("knowledge_reasoning_editor_stale_edit")
        if original.find(old, start + 1) >= 0:
            raise ValueError("knowledge_reasoning_editor_ambiguous_edit")
        spans.append((start, start + len(old), new))
    spans.sort()
    if any(right[0] < left[1] for left, right in zip(spans, spans[1:])):
        raise ValueError("knowledge_reasoning_editor_overlapping_edits")
    derived = original
    for start, end, replacement in reversed(spans):
        derived = derived[:start] + replacement + derived[end:]
    return _receipt(original, derived, patch, final_answer)


def reasoning_segments(original):
    """Number nonblank paragraphs; original gaps retain all separator bytes."""
    _text(original, nonblank=True)
    fingerprint = sha256(original.encode("utf-8")).hexdigest()[:12]
    pattern = r"[^\r\n]*[^\s\r\n][^\r\n]*(?:(?:\r\n|\r|\n)(?=[^\r\n]*[^\s\r\n])[^\r\n]*)*"
    return [{"segment_id": f"p{index:04d}_{fingerprint}", "start": match.start(), "end": match.end(),
             "text": match.group(0)} for index, match in enumerate(re.finditer(pattern, original), 1)]


def _segment_schema(segments):
    if not isinstance(segments, list) or not segments:
        raise ValueError("knowledge_reasoning_editor_invalid_segments")
    identities = [segment.get("segment_id") if isinstance(segment, dict) else None for segment in segments]
    if any(not isinstance(value, str) or not value for value in identities) or len(set(identities)) != len(identities):
        raise ValueError("knowledge_reasoning_editor_invalid_segments")
    schema = _schema()
    schema["properties"]["edits"]["items"] = {"type": "object", "additionalProperties": False,
        "required": ["segment_id", "new_text"], "properties": {
            "segment_id": {"type": "string", "enum": identities}, "new_text": {"type": "string"}}}
    return schema


def knowledge_reasoning_segment_editor_tool(segments):
    return {"type": "function", "function": {"name": EDIT_KNOWLEDGE_REASONING_SEGMENTS, "strict": True,
        "description": "Edit only supplied paragraph IDs. Submit complete replacement text for each changed "
            "paragraph; the program keeps all other paragraphs and separators unchanged. Report unresolved "
            "content problems without claiming quality approval.",
        "parameters": strict_tool_schema(_segment_schema(segments))}}


def apply_segment_edits(original, assistant, *, final_answer=None):
    """Resolve program-owned paragraph IDs, without model copies of old text."""
    segments = reasoning_segments(original)
    if final_answer is not None:
        _text(final_answer, nonblank=True)
    patch = _submission(assistant, EDIT_KNOWLEDGE_REASONING_SEGMENTS, _segment_schema(segments))
    _check_issues(patch["issues"], original, final_answer)
    identities = [edit["segment_id"] for edit in patch["edits"]]
    if len(set(identities)) != len(identities):
        raise ValueError("knowledge_reasoning_editor_duplicate_segment")
    by_id = {segment["segment_id"]: segment for segment in segments}
    replacements, filled = [], []
    for edit in patch["edits"]:
        segment = by_id[edit["segment_id"]]
        new = _text(edit["new_text"])
        if new == segment["text"]:
            raise ValueError("knowledge_reasoning_editor_unchanged_edit")
        replacements.append((segment["start"], segment["end"], new))
        filled.append({"segment_id": edit["segment_id"], "old_text": segment["text"], "new_text": new})
    derived = original
    for start, end, replacement in sorted(replacements, reverse=True):
        derived = derived[:start] + replacement + derived[end:]
    return _receipt(original, derived, {**patch, "edits": filled}, final_answer, version=2)


def prepare_self_edit_messages(original_messages, original_assistant, instruction):
    """Append a real completed response and application note, never fake history."""
    if (not isinstance(original_messages, list) or not original_messages
            or any(not isinstance(message, dict) or message.get("role") not in {
                "system", "developer", "user", "assistant", "tool"} for message in original_messages)):
        raise ValueError("knowledge_reasoning_editor_invalid_original_prefix")
    for message in original_messages:
        if message["role"] == "assistant" and not isinstance(message.get("reasoning_content"), str):
            raise ValueError("knowledge_reasoning_editor_history_reasoning_missing")
    if issue := native_response_issue(original_assistant, require_answer=True):
        raise ValueError(issue)
    if original_assistant.get("tool_calls") not in (None, []):
        raise ValueError("knowledge_reasoning_editor_original_tool_call_pending")
    _text(original_assistant["reasoning_content"], nonblank=True)
    _text(original_assistant["content"], nonblank=True)
    _text(instruction, nonblank=True)
    messages = [*deepcopy(original_messages), deepcopy(original_assistant),
                {"role": "system", "content": instruction}]
    receipt = {"version": 1, "mode": "original_context_reasoning_self_edit_preparation",
        "prefix_sha256": dialogue_digest(original_messages), "prefix_message_count": len(original_messages),
        "original_assistant_sha256": dialogue_digest(original_assistant),
        "original_reasoning_sha256": sha256(original_assistant["reasoning_content"].encode("utf-8")).hexdigest(),
        "fixed_answer_sha256": sha256(original_assistant["content"].encode("utf-8")).hexdigest(),
        "instruction_sha256": sha256(instruction.encode("utf-8")).hexdigest(),
        "messages_sha256": dialogue_digest(messages), "original_prefix_preserved": True,
        "reasoning_scope": "full_original_reasoning_content", "answer_scope": "original_content_read_only",
        "added_roles": ["assistant", "system"], "provenance_scope": "message_and_channel_hashes_only",
        "semantic_status": "not_assessed"}
    return messages, {**receipt, "sha256": dialogue_digest(receipt)}
