"""Recorded Agent edits preserve execution evidence; they do not verify tools."""
from copy import deepcopy
import json

from jsonschema import Draft202012Validator

from lib.domain.reasoning_fields import REASONING_FIELDS, sft_reasoning_issue
from .authored_submission import strict_tool_schema
from .conversation_reasoning import conversation_reasoning_receipt, has_recorded_reasoning
from .dialogue import dialogue_digest
from .knowledge_reasoning_editor import apply_local_edits

AGENT_OPTIMIZATION_PROMPT = "workflow.agent_trajectory_optimization"
AGENT_OPTIMIZATION_TOOL = "edit_recorded_agent_turn"


def agent_optimization_enabled(recipe):
    return type(recipe.get("agent_trajectory_optimization_version")) is int and recipe["agent_trajectory_optimization_version"] == 1


def recorded_agent_issue(sample):
    """Check source protocol and causal pairing without executing recorded tools."""
    messages = sample.get("messages")
    if not isinstance(messages, list) or not messages:
        return "recorded_agent_messages_required"
    pending, seen, has_user = {}, set(), False
    for message in messages:
        if not isinstance(message, dict) or message.get("role") not in {"system", "user", "assistant", "tool"}:
            return "recorded_agent_invalid_role"
        role, content = message["role"], message.get("content")
        calls = message.get("tool_calls", message.get("toolCalls", []))
        if not isinstance(calls, list) or role != "assistant" and calls:
            return "recorded_agent_invalid_tool_calls"
        if content is not None and not isinstance(content, str):
            return "recorded_agent_nontext_content"
        if role in {"system", "user"} and not isinstance(content, str):
            return "recorded_agent_invalid_content"
        if role == "user":
            if not content.strip():
                return "recorded_agent_empty_user"
            has_user = True
        if role != "tool" and pending:
            return "recorded_agent_missing_tool_observation"
        if role == "assistant":
            if not has_user or not calls and (not isinstance(content, str) or not content.strip()):
                return "recorded_agent_incomplete_assistant"
            for call in calls:
                if not isinstance(call, dict) or call.get("type", "function") != "function":
                    return "recorded_agent_invalid_tool_call"
                function, identifier = call.get("function"), call.get("id")
                if (not isinstance(identifier, str) or not identifier or identifier in seen
                        or not isinstance(function, dict) or not isinstance(function.get("name"), str)
                        or not function["name"].strip() or not isinstance(function.get("arguments"), str)):
                    return "recorded_agent_invalid_tool_call"
                try:
                    if not isinstance(json.loads(function["arguments"]), dict):
                        return "recorded_agent_invalid_tool_arguments"
                except ValueError:
                    return "recorded_agent_invalid_tool_arguments"
                pending[identifier] = function["name"]
                seen.add(identifier)
        if role == "tool":
            identifier = message.get("tool_call_id", message.get("toolCallId"))
            if identifier not in pending:
                return "recorded_agent_unpaired_tool_observation"
            name = message.get("name", message.get("toolName"))
            if name is not None and name != pending[identifier]:
                return "recorded_agent_tool_name_mismatch"
            del pending[identifier]
    if pending:
        return "recorded_agent_missing_tool_observation"
    if not has_user or messages[-1].get("role") != "assistant" or messages[-1].get("tool_calls", messages[-1].get("toolCalls")):
        return "recorded_agent_completed_answer_required"
    if not isinstance(sample.get("tools", []), list):
        return "recorded_agent_invalid_tools"
    return sft_reasoning_issue(messages)


def prepare_agent_input(unit, policy):
    """Defer Agent-only supplementation to its one final-turn editor."""
    if unit.get("kind") != "conversation" or unit.get("status") not in (None, "ready") or policy is None:
        return unit
    receipt = conversation_reasoning_receipt(unit["messages"], policy["mode"], version=policy["version"])
    receipt["author_scope"] = "agent_final_assistant_only"
    if policy["mode"] == "exclude" and receipt["missing_turns"]:
        return {**deepcopy(unit), "status": "skipped", "reason": "conversation_reasoning_missing",
                "conversation_reasoning": {**receipt, "status": "excluded"}}
    return {**deepcopy(unit), "conversation_reasoning": {**receipt,
        "status": "deferred_agent_author" if policy["mode"] == "fill_missing" and receipt["missing_turns"] else "preserved"}}


def agent_optimization_input(unit, policy=None, *, message_index=None):
    if issue := recorded_agent_issue(unit):
        raise ValueError(issue)
    messages = unit["messages"]
    index = len(messages) - 1 if message_index is None else message_index
    if type(index) is not int or not 0 <= index < len(messages) or messages[index]["role"] != "assistant":
        raise ValueError("recorded_agent_invalid_edit_index")
    return {"version": 1, "message_index": index, "history": deepcopy(messages[:index]),
        "recorded_response": deepcopy(messages[index]), "tools": deepcopy(unit.get("tools", [])),
        "allow_missing_reasoning": (policy or {}).get("mode") == "fill_missing",
        "source_outcome": "unknown", "tool_execution": "recorded_not_replayed"}


def _closed(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def agent_optimization_tool():
    schema = _closed({
        "status": {"type": "string", "enum": ["unchanged", "edited", "needs_content_repair"]},
        "edits": {"type": "array", "items": _closed({"old_text": {"type": "string"}, "new_text": {"type": "string"}})},
        "missing_reasoning": {"type": "string"},
        "issues": {"type": "array", "items": _closed({"quote": {"type": "string"}, "reason": {"type": "string"}})},
        "flow_suggestions": {"type": "array", "items": _closed({"description": {"type": "string"},
            "message_indexes": {"type": "array", "items": {"type": "integer"}}})},
    })
    return {"type": "function", "function": {"name": AGENT_OPTIMIZATION_TOOL, "strict": True,
        "description": "Edit only the selected recorded reasoning. Flow proposals remain unexecuted and require verification.",
        "parameters": strict_tool_schema(schema)}}


def _unique(pairs):
    value = {}
    for key, child in pairs:
        if key in value:
            raise ValueError("agent_optimization_duplicate_field")
        value[key] = child
    return value


def apply_agent_optimization(unit, payload, assistant):
    """Build one derivative; the model cannot submit answers or tool mutations."""
    calls = assistant.get("tool_calls") if isinstance(assistant, dict) else None
    if (not isinstance(assistant, dict) or assistant.get("role") != "assistant"
            or assistant.get("content") not in (None, "") or not isinstance(calls, list) or len(calls) != 1
            or not isinstance(calls[0], dict) or not isinstance(calls[0].get("function"), dict)
            or calls[0]["function"].get("name") != AGENT_OPTIMIZATION_TOOL):
        raise ValueError("agent_optimization_invalid_submission")
    try:
        patch = json.loads(calls[0]["function"]["arguments"], object_pairs_hook=_unique)
    except (KeyError, TypeError, json.JSONDecodeError):
        raise ValueError("agent_optimization_invalid_submission") from None
    if not Draft202012Validator(agent_optimization_tool()["function"]["parameters"]).is_valid(patch):
        raise ValueError("agent_optimization_invalid_schema")
    for suggestion in patch["flow_suggestions"]:
        if not suggestion["description"].strip() or any(not 0 <= index <= payload["message_index"] for index in suggestion["message_indexes"]):
            raise ValueError("agent_optimization_future_reference")
    index = payload["message_index"]
    original = unit["messages"][index]
    if payload["recorded_response"] != original or payload["history"] != unit["messages"][:index]:
        raise ValueError("agent_optimization_source_changed")
    base = next((original[field] for field in REASONING_FIELDS if isinstance(original.get(field), str) and original[field].strip()), "")
    supplement = patch["missing_reasoning"]
    if not isinstance(supplement, str) or "\x00" in supplement:
        raise ValueError("agent_optimization_invalid_text")
    if base:
        if supplement:
            raise ValueError("agent_optimization_existing_reasoning_replacement")
        literal = {key: deepcopy(patch[key]) for key in ("status", "edits", "issues")}
        submitted = {"role": "assistant", "content": "", "tool_calls": [{"function": {
            "name": "edit_knowledge_reasoning", "arguments": json.dumps(literal, ensure_ascii=False)}}]}
        derived, patch_receipt = apply_local_edits(base, submitted,
            final_answer=original.get("content") or None)
    else:
        if patch["edits"] or supplement and not payload["allow_missing_reasoning"]:
            raise ValueError("agent_optimization_missing_reasoning_policy")
        if (patch["status"] == "edited") != bool(supplement.strip()) and patch["status"] != "needs_content_repair":
            raise ValueError("agent_optimization_status_mismatch")
        for issue in patch["issues"]:
            if not issue["reason"].strip() or issue["quote"] and issue["quote"] not in (original.get("content") or ""):
                raise ValueError("agent_optimization_unbound_issue")
        derived, patch_receipt = supplement, None
    messages = deepcopy(unit["messages"])
    changed = derived != base
    if changed:
        aliases = [field for field in REASONING_FIELDS if field in original and original[field] not in (None, "")]
        for field in aliases or ["reasoning_content"]:
            messages[index][field] = derived
    body = {"version": 1, "status": patch["status"], "message_index": index,
        "source_messages_sha256": dialogue_digest(unit["messages"]), "derived_messages_sha256": dialogue_digest(messages),
        "fixed_answer_sha256": dialogue_digest(unit["messages"][-1].get("content")),
        "tools_sha256": dialogue_digest(unit.get("tools", [])), "changed": changed,
        "supplemented_missing_reasoning": changed and not bool(base),
        "remaining_missing_turns": sum(message.get("role") == "assistant" and not has_recorded_reasoning(message) for message in messages),
        "edits": deepcopy(patch["edits"]), "issues": deepcopy(patch["issues"]),
        "flow_suggestions": [{**deepcopy(suggestion), "status": "requires_verification", "executed": False}
                             for suggestion in patch["flow_suggestions"]],
        "reasoning_edit": patch_receipt, "reasoning_origin": "synthetic_explanation" if changed else "source" if base else "missing",
        "execution_evidence": "recorded_preserved_not_replayed", "final_answer": "byte_preserved",
        "source_outcome": "unknown", "semantic_status": "not_assessed"}
    return messages, {**body, "sha256": dialogue_digest(body)}


def valid_agent_optimization_record(row):
    """Authenticate exact recorded consistency, never tool success or semantics."""
    try:
        receipt, source = row["agent_optimization"], row["source_context"]
        if (type(receipt.get("version")) is not int or receipt["version"] != 1
                or row.get("id") != source.get("id") or row.get("source_id") != source.get("source_id")
                or receipt.get("status") not in {"unchanged", "edited", "needs_content_repair"}
                or receipt.get("sha256") != dialogue_digest({key: value for key, value in receipt.items() if key != "sha256"})
                or receipt["source_messages_sha256"] != dialogue_digest(source["messages"])
                or row.get("original_messages_sha256") != receipt["source_messages_sha256"]
                or receipt["derived_messages_sha256"] != dialogue_digest(row["messages"])
                or receipt["fixed_answer_sha256"] != dialogue_digest(row["messages"][-1].get("content"))
                or receipt["tools_sha256"] != dialogue_digest(row.get("tools", []))
                or source.get("tools", []) != row.get("tools", [])
                or len(source["messages"]) != len(row["messages"])):
            return False
        selected = receipt["message_index"]
        for index, (before, after) in enumerate(zip(source["messages"], row["messages"])):
            if index != selected:
                if before != after:
                    return False
            elif {key: value for key, value in before.items() if key not in REASONING_FIELDS} != {
                    key: value for key, value in after.items() if key not in REASONING_FIELDS}:
                return False
        return recorded_agent_issue(row) is None
    except (KeyError, IndexError, TypeError, ValueError):
        return False


def agent_optimization_surface_issue(row):
    """Only newly edited CoT is checked; recorded source/answer stay untouched."""
    receipt = row["agent_optimization"]
    if not receipt.get("changed"):
        return None
    from lib.domain.training_surface import training_surface_internal_process_issue
    index = receipt["message_index"]
    message = row["messages"][index]
    reasoning = next((message[field] for field in REASONING_FIELDS if message.get(field)), "")
    original = row["source_context"]["messages"][index]
    old_reasoning = next((original[field] for field in REASONING_FIELDS if original.get(field)), "")
    visible = row["source_context"]["messages"][:index]
    issue = training_surface_internal_process_issue(reasoning, visible_messages=visible)
    return issue if issue and not training_surface_internal_process_issue(old_reasoning, visible_messages=visible) else None
