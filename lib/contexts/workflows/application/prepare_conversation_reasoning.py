"""Fill only explicitly requested missing turns, without rewriting source actions."""
from __future__ import annotations

from copy import deepcopy
from typing import Protocol

from lib.domain.reasoning_fields import REASONING_FIELDS, sft_reasoning_issue
from ..domain.conversation_reasoning import (
    conversation_reasoning_receipt, generated_reasoning_issue, generated_hints_issue,
)
from ..domain.dialogue import dialogue_digest


class ConversationReasoningPort(Protocol):
    def request(self, key: list, role: str, prompt_id: str, payload: dict, **options): ...
    def check_reasoning_cancel(self) -> None: ...


class PrepareConversationReasoning:
    """Apply a frozen import policy; model output is a synthetic explanation only."""

    def __init__(self, port: ConversationReasoningPort, policy: dict | None):
        self._port = port
        self._policy = policy

    def execute_many(self, units):
        """Preserve input streaming and leave non-conversation units untouched."""
        iterator = iter(units)
        try:
            for unit in iterator:
                yield self.execute(unit)
        finally:
            # An interrupted request must release the upstream iterator's
            # resources before a retry opens the same source in another worker.
            close = getattr(iterator, "close", None)
            if close is not None:
                close()

    def execute(self, unit: dict) -> dict:
        if self._policy is None or unit.get("kind") != "conversation" or unit.get("status") != "ready":
            return unit
        original_messages = unit["messages"]
        mode = self._policy["mode"]
        version = self._policy["version"]
        receipt = conversation_reasoning_receipt(original_messages, mode, version=version)
        prepared = {**unit, "conversation_reasoning": receipt}
        if mode == "preserve":
            return prepared
        if mode == "exclude":
            if receipt["missing_turns"]:
                receipt["status"] = "excluded"
                prepared.update(status="skipped", reason="conversation_reasoning_missing")
            return prepared
        # A conflicting or non-text alias is malformed source evidence, not a
        # missing explanation that can be silently replaced with purchased text.
        if issue := sft_reasoning_issue(original_messages):
            receipt["status"] = "incomplete"
            prepared.update(status="quarantined", reason=issue)
            return prepared
        if not receipt["missing_turns"]:
            receipt["status"] = "completed"
            return prepared
        prepared["messages"] = deepcopy(original_messages)
        for turn in receipt["turns"]:
            if turn["origin"] != "missing":
                continue
            self._port.check_reasoning_cancel()
            index = turn["message_index"]
            # Each turn gets the immutable original prefix. Later observations,
            # source metadata and earlier synthetic fills are not evidence.
            payload = {"history": deepcopy(original_messages[:index]),
                       "assistant_response": deepcopy(original_messages[index]),
                       "tools": deepcopy(unit.get("tools", [])),
                       "source_scope": deepcopy(turn["source_scope"])}
            key = [unit["id"], "conversation_reasoning_v1", index]
            if version >= 2:
                # record.tools can merge definitions from future turns. These
                # reconstruction requests explain actions; they do not execute
                # tools and need neither the merged registry nor its schemas.
                payload.pop("tools")
                if version == 3:
                    payload["response_kind"] = (
                        "tool_action" if original_messages[index].get("tool_calls") else "answer")
                namespace = f"conversation_reasoning_v{version}"
                hint_key = [unit["id"], namespace, index, "hints", dialogue_digest(payload)]
                hints = self._request(hint_key, "workflow.conversation_reasoning_hints", payload)
                turn["hints"] = {"status": "proposed", "semantic_status": "not_assessed",
                                 "raw_response": deepcopy(hints)}
                self._capture_checkpoint(turn["hints"], hints)
                if issue := generated_hints_issue(hints, original_messages[index]):
                    turn["hints"].update(status="failed", reason=issue)
                    return self._quarantine(prepared, turn, issue, hints)
                # Build a new allowlisted request. No original answer, hint
                # author's native reasoning or cross-turn synthetic text can
                # enter the second model context through a copied envelope.
                payload = {"history": deepcopy(original_messages[:index]),
                           "reasoning_hints": hints["reasoning_hints"],
                           "source_scope": deepcopy(turn["source_scope"]),
                           **({"response_kind": payload["response_kind"]} if version == 3 else {})}
                key = [unit["id"], namespace, index, "reasoning",
                       dialogue_digest(payload)]
                self._port.check_reasoning_cancel()
            response = self._request(key, "workflow.conversation_reasoning", payload)
            self._capture_checkpoint(turn, response)
            if issue := generated_reasoning_issue(response):
                return self._quarantine(prepared, turn, issue, response)
            message = prepared["messages"][index]
            for field in REASONING_FIELDS:
                message.pop(field, None)  # Only this turn's absent/blank aliases.
            message["reasoning_content"] = response["reasoning_content"]
            turn.update(origin="synthetic_explanation", status="synthesized")
            receipt["synthesized_turns"] += 1
            receipt["remaining_missing_turns"] -= 1
            prepared["reasoning_origin"] = "synthetic_explanation"
        receipt["status"] = "completed"
        return prepared

    def _request(self, key, prompt_id, payload):
        return self._port.request(
            key, "generation", prompt_id, payload,
            model_stage="ingest", prompt_stage="ingest",
            allow_reasoning_fallback=False, capture_response_details=True,
            native_request_attempts=1,
        )

    @staticmethod
    def _capture_checkpoint(target, response):
        details = getattr(response, "response_details", None)
        if isinstance(details, dict) and isinstance(details.get("checkpoint"), dict):
            target["request"] = deepcopy(details["checkpoint"])

    @staticmethod
    def _quarantine(prepared, turn, issue, response):
        turn.update(status="failed", reason=issue, raw_response=deepcopy(response))
        prepared["conversation_reasoning"]["status"] = "incomplete"
        prepared.update(status="quarantined", reason=issue)
        return prepared
