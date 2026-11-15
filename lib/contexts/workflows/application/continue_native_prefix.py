"""One native beta continuation using already recorded editorial evidence."""
from copy import deepcopy

from ..domain.native_prefix_feedback import (
    complete_native_prefix_feedback, native_prefix_messages, prepare_native_prefix_feedback,
    prepare_native_prefix_finalization,
)
from ..domain.reasoning_checklist import checklist_digest


class ContinueNativePrefix:
    """The port authenticates the original model and owns durable requests."""

    def __init__(self, port, *, reflection_seed="", finalize_empty_answer=False):
        self.port = port
        self.reflection_seed = reflection_seed
        self.finalize_empty_answer = finalize_empty_answer

    def execute_from_incomplete(self, key, previous_receipt):
        """Finish one saved STOP response that supplied reasoning but no answer.

        This is a separate, bounded answer phase, not a repeat of reasoning or
        a semantic assessment. The first paid response remains immutable.
        """
        try:
            prepared = prepare_native_prefix_finalization(previous_receipt)
        except ValueError as error:
            return None, {**deepcopy(previous_receipt), "finalization_issue": str(error)}
        identity = checklist_digest(previous_receipt)
        self.port.check_reasoning_cancel()
        response = self.port.finalize_prefix([*key, "native_prefix_final_answer_v1", identity],
            prepared["session"], prepared["pending_messages"],
            reasoning_prefix=prepared["reasoning_prefix"])
        try:
            return complete_native_prefix_feedback(previous_receipt["original_session"],
                previous_receipt["original_assistant"], previous_receipt["findings"],
                previous_receipt["transcript"][0]["response"],
                reflection_seed=previous_receipt.get("reflection_seed", ""), final_response=response)
        except ValueError as error:
            return None, {**deepcopy(previous_receipt), "finalization_issue": str(error),
                "model_turns": 2, "finalization_response": deepcopy(response)}

    def execute(self, key, session, assistant, *, findings):
        incomplete = {"version": 1, "mode": "same_author_native_feedback", "guidance_version": 8,
            "feedback_request_origin": "system_review_guidance",
            "continuation_transport": "deepseek_chat_beta", "status": "incomplete", "issue": None,
            "semantic_status": "not_assessed", "independent_review": False, "original_preserved": True,
            "model_turns": 0, "original_session": deepcopy(session),
            "original_assistant": deepcopy(assistant), "findings": deepcopy(findings),
            "transcript": [], "reasoning_segments": []}
        try:
            note, _ = prepare_native_prefix_feedback(session, assistant, findings, reflection_seed=self.reflection_seed)
        except ValueError as error:
            incomplete["issue"] = str(error)
            return None, incomplete
        pending = [*deepcopy(session["messages"]), note]
        prefix = assistant["reasoning_content"] + self.reflection_seed
        identity = checklist_digest([session, assistant, findings, pending, prefix])
        self.port.check_reasoning_cancel()
        response = self.port.continue_prefix([*key, "native_prefix_feedback_v1", identity],
            session, deepcopy(pending), reasoning_prefix=prefix)
        incomplete.update(model_turns=1, reasoning_prefix=prefix, guidance_message=deepcopy(note),
            request_messages=native_prefix_messages(pending, prefix),
            response_details=deepcopy(response.get("response_details", {})) if isinstance(response, dict) else {},
            transcript=[{"phase": "continue_answer", "response": deepcopy(response), "observations": []}])
        if self.reflection_seed:
            incomplete["reflection_seed"] = self.reflection_seed
        try:
            return complete_native_prefix_feedback(session, assistant, findings, response,
                reflection_seed=self.reflection_seed)
        except ValueError as error:
            incomplete["issue"] = str(error)
            if (self.finalize_empty_answer is True
                    and incomplete["issue"] == "native_feedback_answer_missing"):
                return self.execute_from_incomplete(key, incomplete)
            return None, incomplete
