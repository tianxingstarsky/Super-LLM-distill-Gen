"""Regenerate one native answer with learner-visible notes after the real user."""
from copy import deepcopy

from ..domain.native_feedback import native_response_issue
from ..domain.reasoning_checklist import checklist_digest
from ..domain.review_guidance import (
    REVIEW_GUIDANCE_ORIGIN, REVIEW_GUIDANCE_SCOPE, REVIEW_GUIDANCE_PARENT_RECEIPT,
    prepare_review_guidance, restore_review_guidance_session,
)


class RegenerateWithReviewGuidance:
    def __init__(self, port):
        self.port = port

    def execute(self, key, session, assistant, *, findings):
        parent_receipt = session.get(REVIEW_GUIDANCE_PARENT_RECEIPT) if isinstance(session, dict) else None
        receipt = {"version": 1, "mode": "same_author_native_feedback", "guidance_version": 7,
            "feedback_request_origin": REVIEW_GUIDANCE_ORIGIN, "status": "incomplete", "issue": None,
            "semantic_status": "not_assessed", "independent_review": False, "original_preserved": True,
            "model_turns": 0, "original_session": deepcopy(session), "original_assistant": deepcopy(assistant),
            "findings": deepcopy(findings), "transcript": [], "reasoning_segments": []}
        if parent_receipt is not None:
            receipt.update(previous_receipt=deepcopy(parent_receipt),
                           previous_receipt_sha256=checklist_digest(parent_receipt))
        try:
            session = restore_review_guidance_session(session, assistant)
            receipt["original_session"] = deepcopy(session)
            note, observation = prepare_review_guidance(session, assistant, findings)
        except ValueError as error:
            receipt["issue"] = str(error)
            return None, receipt
        history = [*deepcopy(session["messages"]), note]
        receipt.update(author=session["author"], stage=session["stage"],
            original_session_sha256=checklist_digest(session), observation=observation,
            guidance_message=deepcopy(note), tools_preserved=True)
        if "recovery" in session:
            receipt["session_recovery"] = deepcopy(session["recovery"])
        identity_fields = [session, assistant, findings, note]
        if parent_receipt is not None:
            identity_fields.append({"previous_receipt_sha256": receipt["previous_receipt_sha256"]})
        identity = checklist_digest(identity_fields)
        self.port.check_reasoning_cancel()
        response = self.port.turn([*key, "review_guidance_v1", identity, "continue_answer"],
            session, deepcopy(history), deepcopy(session["tools"]), final=True)
        receipt["model_turns"] = 1
        receipt["transcript"].append({"phase": "continue_answer", "response": deepcopy(response), "observations": []})
        revised = response.get("assistant") if isinstance(response, dict) else None
        details = response.get("response_details", {}) if isinstance(response, dict) else {}
        issue = native_response_issue(revised, details, require_answer=True)
        if issue or revised.get("tool_calls"):
            receipt["issue"] = issue or "review_guidance_unexpected_tool_call"
            return None, receipt
        value = {"reasoning": revised["reasoning_content"], "answer": revised["content"]}
        receipt.update(status="completed", revised_assistant=deepcopy(revised),
            candidate_sha256=checklist_digest(value), response_details=deepcopy(details),
            continuation_session={**deepcopy(session), "messages": history}, reasoning_scope=REVIEW_GUIDANCE_SCOPE,
            reasoning_segments=[{"phase":"continue_answer", "origin":"provider_reasoning_content", "text":value["reasoning"]}])
        return value, receipt
