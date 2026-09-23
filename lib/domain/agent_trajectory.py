"""Bounded, side-effect-free replay of recorded Agent tool trajectories.

Only explicitly registered deterministic tools run here. In particular, a
recorded web, file, shell, or code result is not execution evidence.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import re
from typing import Callable, Protocol

from lib.domain.math_tasks import evaluate_integer_expression
from lib.domain.multiturn import completed_turn_ends
from lib.domain.workflow_quality import canonical, conversation_issue


MAX_SNAPSHOTS = 16
MAX_SNAPSHOT_CHARS = 32_768
MAX_SNAPSHOT_NODES = 2_048
MAX_SNAPSHOT_DEPTH = 16
REPLAY_POLICY_VERSION = "bounded-agent-replay-v3"
_UNSET = object()
_ARRAY_INDEX = re.compile(r"0|[1-9][0-9]*")


class ReplayUnavailable(ValueError):
    """The recorded input falls outside a registered replay contract."""


@dataclass(frozen=True)
class ReplayOutcome:
    value: object
    method: str
    evidence: dict


class SandboxReplayPort(Protocol):
    """An optional isolated executor; unknown tools remain unsupported."""

    def replay(self, name: str, arguments: dict, snapshots: object) -> ReplayOutcome: ...

    def terminal(self) -> tuple[bool, dict]: ...


def _calls(message: dict) -> list[dict]:
    return message.get("tool_calls", message.get("toolCalls", [])) or []


def _call_parts(call: dict) -> tuple[str, dict]:
    function = call.get("function") or call
    name = function["name"]
    if not isinstance(name, str) or not name.strip():
        raise ValueError("invalid_tool_name")
    arguments = function.get("arguments", function.get("input", function.get("args", {})))
    if isinstance(arguments, str):
        arguments = json.loads(arguments)
    if not isinstance(arguments, dict):
        raise ValueError("invalid_tool_arguments")
    return name, arguments


def _snapshot_digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def validate_tool_snapshots(snapshots: object) -> None:
    if snapshots is None:
        return
    if not isinstance(snapshots, dict) or len(snapshots) > MAX_SNAPSHOTS:
        raise ReplayUnavailable("invalid_tool_snapshots")
    nodes = 0
    stack = [(snapshots, 0)]
    while stack:
        value, depth = stack.pop()
        nodes += 1
        if nodes > MAX_SNAPSHOT_NODES or depth > MAX_SNAPSHOT_DEPTH:
            raise ReplayUnavailable("tool_snapshot_limit_exceeded")
        if isinstance(value, dict):
            if any(not isinstance(key, str) or len(key) > 256 for key in value):
                raise ReplayUnavailable("invalid_tool_snapshots")
            stack.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            stack.extend((child, depth + 1) for child in value)
        elif type(value) is str:
            if len(value) > 4_096:
                raise ReplayUnavailable("tool_snapshot_limit_exceeded")
        elif type(value) is float:
            if not math.isfinite(value):
                raise ReplayUnavailable("invalid_tool_snapshots")
        elif value is not None and type(value) not in {int, bool}:
            raise ReplayUnavailable("invalid_tool_snapshots")
    if any(not key or len(key) > 64 for key in snapshots):
        raise ReplayUnavailable("invalid_tool_snapshots")
    if len(canonical(snapshots)) > MAX_SNAPSHOT_CHARS:
        raise ReplayUnavailable("tool_snapshot_limit_exceeded")


def _replay_calculator(arguments: dict, snapshots: object) -> ReplayOutcome:
    expression = arguments.get("expression")
    if set(arguments) != {"expression"} or not isinstance(expression, str):
        raise ReplayUnavailable("invalid_calculator_arguments")
    if len(expression) > 256:
        raise ReplayUnavailable("unsupported_calculator_expression")
    try:
        value = evaluate_integer_expression(expression)
    except (ValueError, SyntaxError, RecursionError):
        raise ReplayUnavailable("unsupported_calculator_expression") from None
    return ReplayOutcome(value, "bounded_arithmetic_replay", {})


def _pointer_token(raw: str) -> str:
    if re.search(r"~(?![01])", raw):
        raise ReplayUnavailable("invalid_json_pointer")
    return raw.replace("~1", "/").replace("~0", "~")


def _replay_json_pointer(arguments: dict, snapshots: object) -> ReplayOutcome:
    """Resolve RFC 6901 tokens against a source-file snapshot, never a call argument."""
    if set(arguments) != {"snapshot_id", "pointer"}:
        raise ReplayUnavailable("invalid_json_pointer_arguments")
    snapshot_id, pointer = arguments["snapshot_id"], arguments["pointer"]
    if not isinstance(snapshot_id, str) or not isinstance(pointer, str):
        raise ReplayUnavailable("invalid_json_pointer_arguments")
    if not isinstance(snapshots, dict) or snapshot_id not in snapshots:
        raise ReplayUnavailable("missing_tool_snapshot")
    if len(pointer) > 256 or (pointer and not pointer.startswith("/")):
        raise ReplayUnavailable("invalid_json_pointer")
    tokens = [] if not pointer else [_pointer_token(token) for token in pointer[1:].split("/")]
    if len(tokens) > MAX_SNAPSHOT_DEPTH:
        raise ReplayUnavailable("invalid_json_pointer")
    value = snapshots[snapshot_id]
    for token in tokens:
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif isinstance(value, list) and _ARRAY_INDEX.fullmatch(token) and int(token) < len(value):
            value = value[int(token)]
        else:
            raise ReplayUnavailable("json_pointer_target_missing")
    if type(value) not in {str, int, bool, type(None)} or (type(value) is str and len(value) > 256):
        raise ReplayUnavailable("unsupported_json_pointer_value")
    return ReplayOutcome(value, "snapshot_json_pointer_replay",
                         {"snapshot_id": snapshot_id, "snapshot_sha256": _snapshot_digest(snapshots[snapshot_id])})


# Adding an adapter requires a bounded input contract and a result comparator.
# No adapter is allowed to read the live filesystem, access the network, or run code.
REPLAY_ADAPTERS: dict[str, Callable[[dict, object], ReplayOutcome]] = {
    "calculator": _replay_calculator,
    "json_pointer": _replay_json_pointer,
}


def _observation_value(content: str) -> object:
    """Accept a JSON scalar or exactly {"result": scalar}; preserve type."""
    try:
        value = json.loads(content)
    except (ValueError, TypeError):
        return _UNSET
    if isinstance(value, dict):
        if set(value) != {"result"}:
            return _UNSET
        value = value["result"]
    return value if type(value) in {str, int, bool, type(None)} else _UNSET


def _numeric_final(content: str) -> int | None:
    text = content.strip()
    matched = re.fullmatch(r"(?:结果|答案)(?:是|为|\s*[:：])?\s*(-?\d+)[。.!]?", text)
    if matched:
        return int(matched.group(1))
    return int(text) if re.fullmatch(r"-?\d+", text) else None


def _final_comparison(content: str, expected: object) -> bool | None:
    """Return None when the answer cannot be compared without interpretation."""
    text = content.strip()
    if type(expected) is int:
        observed = _numeric_final(text)
        return observed == expected if observed is not None else None
    if type(expected) is str:
        if text == expected:
            return True
        try:
            observed = json.loads(text)
        except ValueError:
            return None
        return observed == expected if type(observed) is str else None
    if type(expected) is bool:
        return (text == "true") == expected if text in {"true", "false"} else None
    return text == "null" if text == "null" else None


def _negative(messages: list[dict], index: int, reason: str, evidence: dict) -> dict:
    return {"messages": deepcopy(messages[:index + 1]), "failure": reason,
            "failure_step": index, "evidence": evidence, "outcome": "observed_failure"}


def _prune_identical_pairs(messages: list[dict]) -> tuple[list[dict], list[str]]:
    """Remove only adjacent identical, silent tool-call/result pairs.

    All calls have already been replayed before pruning. The original trace
    stays in the quality record; a removed call ID must not be used later.
    """
    kept: list[dict] = []
    removed: list[str] = []
    previous_signature = None
    index = 0
    while index < len(messages):
        message = messages[index]
        if (message.get("role") == "assistant" and len(_calls(message)) == 1
                and index + 1 < len(messages) and messages[index + 1].get("role") == "tool"
                and not message.get("content") and not message.get("reasoning_content")):
            call = _calls(message)[0]
            result = messages[index + 1]
            if result.get("tool_call_id", result.get("toolCallId")) == call.get("id"):
                call_without_id = {key: value for key, value in call.items() if key != "id"}
                result_without_link = {key: value for key, value in result.items()
                                       if key not in {"tool_call_id", "toolCallId"}}
                assistant_without_calls = {key: value for key, value in message.items()
                                           if key not in {"tool_calls", "toolCalls"}}
                signature = (canonical(assistant_without_calls), canonical(call_without_id),
                             canonical(result_without_link))
                call_id = call["id"]
                later_data = canonical(messages[index + 2:])
                if signature == previous_signature and call_id not in later_data:
                    removed.append(call_id)
                    index += 2
                    continue
                previous_signature = signature
                kept.extend((deepcopy(message), deepcopy(result)))
                index += 2
                continue
        previous_signature = None
        kept.append(deepcopy(message))
        index += 1
    if conversation_issue(kept):
        return deepcopy(messages), []
    return kept, removed


def assess_recorded_trajectory(messages: list[dict], *, tool_snapshots: object = None,
                               source_snapshot_sha256: str | None = None,
                               sandbox_replay: SandboxReplayPort | None = None) -> dict:
    """Replay registered tools and classify one complete recorded trajectory.

    A source-reported error without a successful local replay is not a
    deterministic negative. Unparseable observations and ambiguous prose are
    quarantined for the same reason.
    """
    structural = deepcopy(messages)
    for message in structural:
        if isinstance(message, dict) and message.get("role") == "tool":
            message.pop("isError", None)
    issue = conversation_issue(structural)
    if issue:
        return {"status": "quarantined", "reason": issue}
    _, issue = completed_turn_ends(structural, minimum_turns=1)
    if issue:
        return {"status": "quarantined", "reason": issue}
    try:
        validate_tool_snapshots(tool_snapshots)
    except ReplayUnavailable as error:
        return {"status": "quarantined", "reason": str(error)}

    calls: dict[str, tuple[str, dict]] = {}
    verified: list[str] = []
    call_evidence: list[dict] = []
    turn_result: object = _UNSET
    turn_values: list[object] = []
    verified_turns: list[int] = []
    turn_number = 0
    sandbox_used = False
    for index, message in enumerate(messages):
        if message["role"] == "user":
            turn_number += 1
            turn_result = _UNSET
            turn_values = []
        if message["role"] == "assistant":
            message_calls = _calls(message)
            if not isinstance(message_calls, list) or any(not isinstance(call, dict) for call in message_calls):
                return {"status": "quarantined", "reason": "invalid_tool_calls"}
            if message_calls and message.get("content", "").strip():
                return {"status": "quarantined", "reason": "unverified_tool_call_text"}
            for call in message_calls:
                try:
                    calls[call["id"]] = _call_parts(call)
                except (KeyError, ValueError, TypeError, json.JSONDecodeError):
                    return {"status": "quarantined", "reason": "invalid_tool_arguments"}
            if not message_calls:
                if turn_result is _UNSET:
                    return {"status": "quarantined", "reason": "unverified_assistant_turn",
                            "unverified_turn": turn_number}
                if any(type(value) is not type(turn_result) or value != turn_result for value in turn_values):
                    return {"status": "quarantined", "reason": "ambiguous_turn_result",
                            "unverified_turn": turn_number}
                comparison = _final_comparison(message["content"], turn_result)
                if comparison is None:
                    reason = ("final_answer_not_deterministically_verifiable" if index == len(messages) - 1
                              else "intermediate_answer_not_deterministically_verifiable")
                    return {"status": "quarantined", "reason": reason, "unverified_turn": turn_number}
                if not comparison:
                    reason = "final_answer_mismatch" if index == len(messages) - 1 else "intermediate_answer_mismatch"
                    return {"status": "quarantined", "reason": reason,
                            "negative": _negative(messages, index, reason,
                                                  {"expected": turn_result,
                                                   "observed": (_numeric_final(message["content"])
                                                                if type(turn_result) is int
                                                                else message["content"].strip()),
                                                   "turn": turn_number, "basis": "last_verified_tool_result_in_turn"})}
                verified_turns.append(turn_number)
        if message["role"] != "tool":
            continue
        call_id = message.get("tool_call_id", message.get("toolCallId"))
        name, arguments = calls[call_id]
        recorded_name = message.get("toolName", message.get("name"))
        if recorded_name is not None and recorded_name != name:
            return {"status": "quarantined", "reason": "tool_result_name_mismatch"}
        adapter = REPLAY_ADAPTERS.get(name)
        if adapter is None and (name != "sandbox_ledger" or sandbox_replay is None):
            return {"status": "quarantined", "reason": "tool_replay_unavailable", "unverified_tool": name}
        try:
            if adapter is not None:
                outcome = adapter(arguments, tool_snapshots)
            else:
                outcome = sandbox_replay.replay(name, arguments, tool_snapshots)
                sandbox_used = True
        except ReplayUnavailable as error:
            return {"status": "quarantined", "reason": str(error)}
        error_flag = message.get("isError", False)
        if type(error_flag) is not bool:
            return {"status": "quarantined", "reason": "invalid_tool_error_flag"}
        if error_flag:
            return {"status": "quarantined", "reason": "recorded_tool_error",
                    "negative": _negative(messages, index, "recorded_tool_error",
                                          {"tool": name, "call_id": call_id, "expected": outcome.value,
                                           "observed": "isError", "basis": outcome.method, **outcome.evidence})}
        observed = _observation_value(message.get("content", ""))
        if observed is _UNSET or type(observed) is not type(outcome.value):
            return {"status": "quarantined", "reason": "unparseable_tool_observation"}
        if observed != outcome.value:
            return {"status": "quarantined", "reason": "tool_observation_mismatch",
                    "negative": _negative(messages, index, "tool_observation_mismatch",
                                          {"tool": name, "call_id": call_id, "expected": outcome.value,
                                           "observed": observed, "basis": outcome.method, **outcome.evidence})}
        verified.append(call_id)
        call_evidence.append({"call_id": call_id, "tool": name, "method": outcome.method, **outcome.evidence})
        turn_result = outcome.value
        turn_values.append(outcome.value)

    if not verified:
        return {"status": "quarantined", "reason": "no_replayable_tool_calls"}
    if sandbox_used:
        try:
            goal_met, terminal_evidence = sandbox_replay.terminal()
        except ReplayUnavailable as error:
            return {"status": "quarantined", "reason": str(error)}
        if not goal_met:
            return {"status": "quarantined", "reason": "terminal_state_mismatch",
                    "negative": _negative(messages, len(messages) - 1,
                                          "terminal_state_mismatch", terminal_evidence)}
    pruned, removed = _prune_identical_pairs(messages)
    methods = {entry["method"] for entry in call_evidence}
    return {"status": "eligible", "messages": pruned,
            "verification": {"policy": REPLAY_POLICY_VERSION,
                             "method": (methods.pop() if len(methods) == 1 else
                                        "mixed_verified_tool_replay" if sandbox_used else "bounded_local_replay"),
                             "verified_call_ids": verified, "verified_turns": verified_turns,
                             "call_evidence": call_evidence,
                             "source_snapshot_sha256": source_snapshot_sha256,
                             "final_result": turn_result, "pruned_call_ids": removed,
                             **({"terminal": terminal_evidence} if sandbox_used else {})}}
