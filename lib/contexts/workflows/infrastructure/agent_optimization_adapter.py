"""Frozen local Google retrieval and the existing strict author transport."""
from copy import deepcopy
import json

from ..domain.agent_trajectory_optimization import AGENT_OPTIMIZATION_PROMPT, agent_optimization_tool
from ..domain.dialogue import dialogue_digest
from .reasoning_agent_adapter import CheckpointedReasoningAgent


class RecordedAgentOptimizationAdapter:
    def __init__(self, runtime, google, evolution):
        self.runtime, self.google, self.evolution = runtime, google, evolution

    def select_experience(self, unit, payload):
        frozen = self.runtime.recipe["agent_reasoning_memory"]
        key = [unit["id"], "agent_reasoning_memory", 1, frozen["sha256"],
               payload["message_index"], dialogue_digest(payload["history"])]

        def select():
            from ..domain.reasoning_bank import edition_reference, freeze_selection
            users = [(index, message["content"]) for index, message in enumerate(payload["history"])
                     if message.get("role") == "user"]
            positions = sorted({users[0][0], users[-1][0]}) if users else []
            current_calls = payload["recorded_response"].get("tool_calls", payload["recorded_response"].get("toolCalls", []))
            query_body = {"user_goals": [{"message_index": index, "content": payload["history"][index]["content"]}
                                         for index in positions],
                "decision_kind": "tool_action" if current_calls else "final_response",
                "recorded_tool_names": sorted({call["function"]["name"] for message in payload["history"]
                    for call in message.get("tool_calls", message.get("toolCalls", []))}),
                "current_tool_names": [call["function"]["name"] for call in current_calls]}
            query = json.dumps(query_body, ensure_ascii=False, sort_keys=True)
            reference = frozen.get("edition_ref")
            selection = {"status": "empty", "matches": [], "receipt": {
                "status": "disabled" if not frozen["enabled"] else "no_adopted_edition", "query_sha256": dialogue_digest(query)}}
            if reference and frozen["enabled"]:
                edition = self.google.repository.get_version(reference["id"])
                if edition_reference(edition) != reference:
                    raise ValueError("agent_reasoning_edition_changed")
                try:
                    selection = self.google.bank.retrieve("reasoning", query, edition_ids=[reference["id"]])
                except ValueError as error:
                    if str(error) != "reasoning_bank_invalid_task":
                        raise
                    selection["receipt"]["status"] = "query_outside_retrieval_capacity"
            config = {scope: {"enabled": value["enabled"], "text": value["text"]}
                      for scope, value in self.runtime.recipe["agent_experience"]["scopes"].items()}
            config["reasoning"] = {key: frozen[key] for key in ("enabled", "text")}
            result = freeze_selection(config, {"reasoning": selection})
            body = {"version": 1, "frozen_memory_sha256": frozen["sha256"],
                "enabled": frozen["enabled"], "text": result["snapshot"]["scopes"]["reasoning"]["text"],
                "selection_receipt": result["selection_receipt"], "edition_ref": deepcopy(reference),
                "query_provenance": {"user_message_indexes": positions, "message_index": payload["message_index"],
                    "history_sha256": dialogue_digest(payload["history"]), "goals_truncated": False,
                    "omitted_fields": ["tool_observation_bodies", "prior_reasoning", "tool_arguments", "source_answer"]}}
            return {**body, "sha256": dialogue_digest(body)}
        return self.runtime.checkpoint(key, select)

    def edit_turn(self, unit, payload, experience):
        from lib.infrastructure.workflow_agent_experience import experience_system
        recipe = deepcopy(self.runtime.recipe)
        recipe["agent_experience"]["scopes"]["reasoning"].update(enabled=experience["enabled"], text=experience["text"])
        system = self.runtime.prompt_text(AGENT_OPTIMIZATION_PROMPT, stage="agent") + experience_system(recipe, AGENT_OPTIMIZATION_PROMPT)
        from lib.domain.workflow_quality import canonical
        messages = [{"role": "system", "content": system}, {"role": "user", "content": canonical(payload)}]
        tools = [agent_optimization_tool()]
        request = self.runtime.checkpoint([unit["id"], "recorded_agent_optimization_request", 1,
            dialogue_digest(messages), dialogue_digest(tools)], lambda: {
                "messages": deepcopy(messages), "tools": deepcopy(tools),
                "experience_sha256": experience["sha256"], "model_stage": "agent"})
        response = CheckpointedReasoningAgent(self.runtime).prepared_tool_turn(
            [unit["id"], "recorded_agent_optimization", 1, payload["message_index"], experience["sha256"]],
            "generation", request["messages"], request["tools"], strict=True,
            capture_details=True, model_stage="agent")
        return {**response, "request": request, "request_sha256": dialogue_digest(request)}

    def record_observation(self, row):
        original = deepcopy(row)
        original.pop("experience_observation", None)
        episode = {"id": original["id"], "target": "agent", "agent_episode_projection_version": 1,
            "conversation": {"messages": deepcopy(original["messages"]), "tools": deepcopy(original["tools"])},
            "agent_optimization": {key: deepcopy(value) for key, value in original["agent_optimization"].items()
                if key in {"version", "status", "message_index", "source_messages_sha256", "derived_messages_sha256",
                           "edits", "issues", "flow_suggestions", "reasoning_origin", "source_outcome", "semantic_status"}},
            "actual_delivery": {"status": original["status"], "reason": original.get("reason")},
            "assessment_meaning": "Recorded consistency and unexecuted process proposals do not establish task success."}
        return self.evolution.record_observation("reasoning", episode,
            run_id=self.runtime.state["id"], outcome="unknown", evidence_status="unverified",
            source_record=original, source_receipt={"kind": "recorded_agent_optimization", "version": 1,
                "recipe_sha256": self.runtime.state.get("recipe_hash")})
