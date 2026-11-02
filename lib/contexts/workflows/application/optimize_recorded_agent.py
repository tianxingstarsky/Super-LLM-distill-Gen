"""One bounded author edits a recorded trajectory; tools are never executed."""
from copy import deepcopy

from lib.contexts.models.contracts import ModelRequestError
from ..domain.agent_trajectory_optimization import agent_optimization_input, apply_agent_optimization, prepare_agent_input
from ..domain.dialogue import dialogue_digest


class OptimizeRecordedAgent:
    def __init__(self, port, policy=None):
        self._port, self._policy = port, policy

    def execute(self, unit, *, message_index=None):
        unit = prepare_agent_input(unit, self._policy)
        row = {key: deepcopy(unit[key]) for key in ("id", "source_id", "source_name", "location", "source_location") if key in unit}
        row.update(messages=deepcopy(unit.get("messages", [])), tools=deepcopy(unit.get("tools", [])),
            source_context=deepcopy(unit), evidence_level="recorded_preserved_not_replayed",
            original_messages_sha256=dialogue_digest(unit.get("messages", [])), source_outcome="unknown", semantic_status="not_assessed")
        if unit.get("status") not in (None, "ready"):
            return [{**row, "status": unit["status"], "reason": unit.get("reason", "agent_source_not_ready"),
                     **({"conversation_reasoning": deepcopy(unit["conversation_reasoning"])} if "conversation_reasoning" in unit else {})}]
        if unit.get("kind") != "conversation":
            return [{**row, "status": "quarantined", "reason": "recorded_tool_trajectory_required"}]
        try:
            payload = agent_optimization_input(unit, self._policy, message_index=message_index)
        except ValueError as error:
            return [{**row, "status": "quarantined", "reason": str(error)}]
        experience = self._port.select_experience(unit, payload)
        response = None
        try:
            response = self._port.edit_turn(unit, payload, experience)
            messages, receipt = apply_agent_optimization(unit, payload, response["assistant"])
            row["messages"] = messages
            row.update(status="needs_attention" if receipt["status"] == "needs_content_repair" else "eligible",
                       reasoning_origin=receipt["reasoning_origin"])
            if receipt["status"] == "needs_content_repair":
                row["reason"] = "agent_source_content_repair_required"
        except (ValueError, ModelRequestError) as error:
            receipt = {"version": 1, "status": "invalid_submission", "message_index": payload["message_index"],
                "source_messages_sha256": row["original_messages_sha256"], "semantic_status": "not_assessed",
                "source_outcome": "unknown", "issue": str(error) if isinstance(error, ValueError) else error.code}
            row.update(status="needs_attention", reason="agent_optimization_invalid_response")
            if getattr(error, "response_details", None):
                response = {"response_details": deepcopy(error.response_details)}
        receipt.update(experience=deepcopy(experience), author_response=deepcopy(response),
            consistency_status="consistency_preserved" if receipt["status"] != "invalid_submission" else "original_preserved",
            author_scope="one_selected_assistant", edited_message_indexes=[payload["message_index"]],
            original_trajectory_retained=True)
        if "conversation_reasoning" in unit:
            row["conversation_reasoning"] = deepcopy(unit["conversation_reasoning"])
            row["conversation_reasoning_scope"] = "input_only"
        receipt.pop("sha256", None)
        receipt["sha256"] = dialogue_digest(receipt)
        row["agent_optimization"] = receipt
        if row["status"] == "eligible":
            from lib.contexts.review.contracts import candidate_payload_issue
            if issue := candidate_payload_issue("agent", row):
                row.update(status="needs_attention", reason=issue)
        row["experience_observation"] = self._port.record_observation(row)
        return [row]
