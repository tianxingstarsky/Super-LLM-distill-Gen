"""Fill only explicitly requested missing turns, without rewriting source actions."""
from __future__ import annotations

from copy import deepcopy
from typing import Protocol

from lib.domain.reasoning_fields import REASONING_FIELDS, sft_reasoning_issue
from ..domain.conversation_reasoning import (
    conversation_reasoning_receipt, generated_reasoning_issue,
)


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
        receipt = conversation_reasoning_receipt(original_messages, mode)
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
            response = self._port.request(
                [unit["id"], "conversation_reasoning_v1", index], "generation",
                "workflow.conversation_reasoning", payload,
                model_stage="ingest", prompt_stage="ingest",
                allow_reasoning_fallback=False, capture_response_details=True,
                native_request_attempts=1,
            )
            details = getattr(response, "response_details", None)
            if isinstance(details, dict) and isinstance(details.get("checkpoint"), dict):
                turn["request"] = deepcopy(details["checkpoint"])
            if issue := generated_reasoning_issue(response):
                turn.update(status="failed", reason=issue, raw_response=deepcopy(response))
                receipt["status"] = "incomplete"
                prepared.update(status="quarantined", reason=issue)
                return prepared
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
