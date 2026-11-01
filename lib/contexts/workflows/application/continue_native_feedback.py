"""Continue one authenticated native author with actual, bounded tool feedback."""
from __future__ import annotations

from copy import deepcopy
import json

from ..domain.native_feedback import (
    feedback_work_instruction, feedback_call_issue, history_tool_ids, native_response_issue, prepare_native_feedback,
)
from ..domain.reasoning_checklist import checklist_digest
from ..domain.native_feedback_provenance import (
    REUSED_FEEDBACK_ORIGIN, REUSED_REASONING_SCOPE, authenticate_native_feedback_receipt,
)


class ContinueNativeFeedback:
    """The port owns author authentication, request admission and durable checkpoints."""

    def __init__(self, port, *, guidance_version=1):
        self.port = port
        self.guidance_version = guidance_version

    def execute(self, key, session, assistant, *, findings):
        if type(self.guidance_version) is int and self.guidance_version == 7:
            from .regenerate_with_review_guidance import RegenerateWithReviewGuidance
            return RegenerateWithReviewGuidance(self.port).execute(key, session, assistant, findings=findings)
        receipt = {"version": 1, "mode": "same_author_native_feedback",
            "status": "incomplete", "issue": None, "semantic_status": "not_assessed",
            "independent_review": False, "model_turns": 0,
            "original_assistant": deepcopy(assistant), "findings": deepcopy(findings),
            "transcript": [], "reasoning_segments": [], "original_preserved": True}

        def incomplete(issue):
            receipt["issue"] = issue
            return None, receipt

        try:
            tools, observation = prepare_native_feedback(session, assistant, findings,
                guidance_version=self.guidance_version)
        except ValueError as error:
            return incomplete(str(error))
        receipt.update(author=session["author"], stage=session["stage"],
            original_session_sha256=checklist_digest(session), observation=deepcopy(observation),
            tools_preserved=tools == session["tools"])
        if self.guidance_version != 1:
            receipt["guidance_version"] = self.guidance_version
        if "recovery" in session:
            receipt["session_recovery"] = deepcopy(session["recovery"])
        identity = checklist_digest([session, assistant, observation, tools])
        history = [*deepcopy(session["messages"]), deepcopy(assistant)]
        calls = assistant.get("tool_calls", [])
        if calls:
            issue = feedback_call_issue(assistant)
            if issue:
                return incomplete(issue)
            if self.guidance_version in {4, 5, 6}:
                # Keep the recorded tool call adjacent to its tool response;
                # application guidance belongs before it, never in a new user turn.
                history.insert(-1, {"role": "system", "content": feedback_work_instruction(self.guidance_version)})
            tool_assistant = assistant
            receipt["feedback_request_origin"] = "recorded_provider_tool_call"
        else:
            # Do not retrofit a call ID onto the already completed response.
            # The author must make a real tool decision before any observation.
            history.append({"role": "system", "content": feedback_work_instruction(self.guidance_version)})
            self.port.check_reasoning_cancel()
            response = self.port.turn([*key, "native_feedback_v1", identity, "read_feedback"],
                session, deepcopy(history), deepcopy(tools), final=False)
            receipt["model_turns"] += 1
            entry = {"phase": "read_feedback", "response": deepcopy(response), "observations": []}
            receipt["transcript"].append(entry)
            tool_assistant, details = self._response_parts(response)
            issue = native_response_issue(tool_assistant, details, require_answer=False)
            if issue:
                return incomplete(issue)
            issue = feedback_call_issue(tool_assistant)
            if issue:
                return incomplete(issue)
            if tool_assistant["tool_calls"][0]["id"] in history_tool_ids(history):
                return incomplete("native_feedback_repeated_tool_call_id")
            receipt["reasoning_segments"].append({"phase": "read_feedback",
                "origin": "provider_reasoning_content", "text": tool_assistant["reasoning_content"]})
            history.append(deepcopy(tool_assistant))
            receipt["feedback_request_origin"] = "new_provider_tool_call"
        call_id = tool_assistant["tool_calls"][0]["id"]
        if receipt["feedback_request_origin"] == "recorded_provider_tool_call" and call_id in history_tool_ids(session["messages"]):
            return incomplete("native_feedback_repeated_tool_call_id")
        feedback_message = {"role": "tool", "tool_call_id": call_id,
            "content": json.dumps(observation, ensure_ascii=False, separators=(",", ":"))}
        history.append(feedback_message)
        receipt["tool_feedback"] = deepcopy(feedback_message)
        if receipt["transcript"]:
            receipt["transcript"][-1]["observations"].append(deepcopy(feedback_message))
        self.port.check_reasoning_cancel()
        response = self.port.turn([*key, "native_feedback_v1", identity, "continue_answer"],
            session, deepcopy(history), deepcopy(tools), final=True)
        receipt["model_turns"] += 1
        receipt["transcript"].append({"phase": "continue_answer", "response": deepcopy(response),
                                      "observations": []})
        revised, details = self._response_parts(response)
        issue = native_response_issue(revised, details, require_answer=True)
        if issue:
            return incomplete(issue)
        if revised.get("tool_calls") not in (None, []):
            return incomplete("native_feedback_tool_call_after_observation")
        receipt["reasoning_segments"].append({"phase": "continue_answer",
            "origin": "provider_reasoning_content", "text": revised["reasoning_content"]})
        value = {"reasoning": "\n\n".join(segment["text"] for segment in receipt["reasoning_segments"]),
                 "answer": revised["content"]}
        receipt.update(status="completed", revised_assistant=deepcopy(revised),
            candidate_sha256=checklist_digest(value), response_details=deepcopy(details),
            continuation_session={**deepcopy(session), "messages": deepcopy(history), "tools": deepcopy(tools)},
            reasoning_scope="new_provider_turns_only_original_and_feedback_preserved_separately")
        return value, receipt

    def execute_from_feedback(self, key, session, assistant, *, findings, previous_receipt):
        """Branch after an authenticated delivered observation, without another tool read."""
        receipt = {"version": 1, "mode": "same_author_native_feedback",
            "status": "incomplete", "issue": None, "semantic_status": "not_assessed",
            "independent_review": False, "model_turns": 0, "guidance_version": self.guidance_version,
            "original_assistant": deepcopy(assistant), "findings": deepcopy(findings),
            "transcript": [], "reasoning_segments": [], "original_preserved": True,
            "previous_receipt": deepcopy(previous_receipt)}

        def incomplete(issue):
            receipt["issue"] = issue
            return None, receipt

        if type(self.guidance_version) is not int or self.guidance_version not in {5, 6}:
            return incomplete("native_feedback_reuse_requires_guidance_v5")
        try:
            parent = authenticate_native_feedback_receipt(previous_receipt)
            if (session != parent["original_session"]
                    or assistant != previous_receipt["original_assistant"]
                    or findings != previous_receipt["findings"]):
                raise ValueError("native_feedback_receipt_provenance_mismatch")
        except ValueError as error:
            return incomplete(str(error))
        parent_digest = checklist_digest(previous_receipt)
        continuation_session = parent["continuation_session"]
        tools = continuation_session["tools"]
        history = [*deepcopy(continuation_session["messages"]),
                   {"role": "system", "content": feedback_work_instruction(self.guidance_version)}]
        receipt.update(author=session["author"], stage=session["stage"],
            original_session_sha256=checklist_digest(session), previous_receipt_sha256=parent_digest,
            observation=deepcopy(previous_receipt["observation"]),
            tool_feedback=deepcopy(previous_receipt["tool_feedback"]),
            tools_preserved=previous_receipt["tools_preserved"],
            feedback_request_origin=REUSED_FEEDBACK_ORIGIN)
        if "recovery" in session:
            receipt["session_recovery"] = deepcopy(session["recovery"])
        identity = checklist_digest([session, assistant, findings, parent_digest, history, tools])
        self.port.check_reasoning_cancel()
        response = self.port.turn([*key, "native_feedback_reuse_v1", identity, "continue_answer"],
            continuation_session, deepcopy(history), deepcopy(tools), final=True)
        receipt["model_turns"] = 1
        receipt["transcript"].append({"phase": "continue_answer", "response": deepcopy(response),
                                      "observations": []})
        revised, details = self._response_parts(response)
        issue = native_response_issue(revised, details, require_answer=True)
        if issue:
            return incomplete(issue)
        if revised.get("tool_calls") not in (None, []):
            return incomplete("native_feedback_tool_call_after_observation")
        receipt["reasoning_segments"].append({"phase": "continue_answer",
            "origin": "provider_reasoning_content", "text": revised["reasoning_content"]})
        value = {"reasoning": revised["reasoning_content"], "answer": revised["content"]}
        receipt.update(status="completed", revised_assistant=deepcopy(revised),
            candidate_sha256=checklist_digest(value), response_details=deepcopy(details),
            continuation_session={**deepcopy(continuation_session), "messages": deepcopy(history)},
            reasoning_scope=REUSED_REASONING_SCOPE)
        return value, receipt

    @staticmethod
    def _response_parts(response):
        if not isinstance(response, dict):
            return None, {}
        return response.get("assistant"), response.get("response_details", {})
