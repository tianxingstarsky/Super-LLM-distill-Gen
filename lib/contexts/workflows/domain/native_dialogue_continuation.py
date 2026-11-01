"""Eligibility for an explicit repair of a failed first native dialogue answer."""
from __future__ import annotations

from copy import deepcopy
from .dialogue import dialogue_digest, without_source_reasoning
from .native_feedback_provenance import authenticate_native_feedback_receipt
from .review_guidance import authenticated_review_guidance_positions, REVIEW_GUIDANCE_PARENT_RECEIPT
from lib.domain.multiturn import completed_turn_ends
from lib.domain.workflow_quality import conversation_issue, text_issue


def is_initial_native_dialogue_failure(record):
    """Recognize this route without treating arbitrary partial dialogues as resumable."""
    return (isinstance(record, dict) and record.get("status") == "quarantined"
        and record.get("failed_turn") == 1 and isinstance(record.get("native_generation"), dict)
        and not record.get("turn_reviews")
        and not any(message.get("role") == "assistant" for message in record.get("accepted_prefix", [])))


def native_dialogue_continuation_input(record, instruction, *, guidance_version=1):
    """Bind the source, initial question and failed answer; never invent missing state."""
    if not is_initial_native_dialogue_failure(record):
        raise ValueError("native_dialogue_continuation_unsupported_checkpoint")
    source = record.get("source_context")
    messages, prompt = record.get("messages"), record.get("question")
    user_positions = [index for index, message in enumerate(prompt or [])
                      if isinstance(message, dict) and message.get("role") == "user"] if isinstance(prompt, list) else []
    public_notes = authenticated_review_guidance_positions(record)
    initial_user = prompt[user_positions[0]] if len(user_positions) == 1 else None
    tail_is_public = (initial_user is not None and all(index in public_notes
        for index in range(user_positions[0] + 1, len(prompt))))
    if (not isinstance(source, dict) or source.get("kind") != "brief"
            or source.get("source_context_pack") or source.get("tools")
            or not isinstance(messages, list) or not isinstance(prompt, list)
            or len(messages) != len(prompt) + 1 or messages[:-1] != prompt
            or not prompt or not tail_is_public
            or any(message.get("role") not in {"system", "user"} for message in prompt)
            or sum(message.get("role") == "user" for message in prompt) != 1
            or messages[-1].get("role") != "assistant"
            or not messages[-1].get("content") or not messages[-1].get("reasoning_content")
            or not source.get("qa_contract")
            or source["qa_contract"].get("question") != initial_user.get("content")):
        raise ValueError("native_dialogue_continuation_incomplete_parent")
    if not isinstance(instruction, str) or guidance_version not in {2, 3, 4, 5, 6, 7} and not instruction.strip():
        raise ValueError("native_dialogue_continuation_feedback_required")
    native = record["native_generation"]
    if "tool_feedback" in native or "review_guidance" in native:
        previous, session = _recorded_feedback_author(record)
        if guidance_version == 7 and "tool_feedback" in native:
            # Preserve the authenticated old continuation for explicit recovery;
            # the new use case chooses its certified original request envelope.
            session = {**deepcopy(session), REVIEW_GUIDANCE_PARENT_RECEIPT: deepcopy(native["tool_feedback"])}
    else:
        previous, session = messages[-1], native.get("author_session")
    feedback_findings = (_saved_native_review_findings(record, previous, instruction)
                         if guidance_version in {2, 3, 4, 5, 6, 7} else None)
    if feedback_findings == []:
        raise ValueError("native_dialogue_continuation_feedback_required")
    return {"source": deepcopy(source), "prompt_messages": deepcopy(prompt),
        "previous_candidate": deepcopy(previous), "feedback": instruction,
        **({"public_system_positions": public_notes} if public_notes else {}),
        **({"feedback_findings": feedback_findings} if feedback_findings is not None else {}),
        **({"author_session": deepcopy(session)} if session else {}),
        "source_sha256": dialogue_digest(source),
        "director_contract_sha256": dialogue_digest(source["qa_contract"]),
        "initial_question_sha256": dialogue_digest(initial_user),
        "parent_candidate_sha256": dialogue_digest(record)}


def _saved_native_review_findings(record, previous, instruction):
    """Reuse the existing bound assessment; never buy or invent a first-person rewrite."""
    from .targets import training_record
    findings = []
    review = record.get("review_repair") or {}
    check, path = None, None
    if isinstance(review.get("verdict"), dict):
        # Only a review of the current complete training surface is reusable.
        if review.get("candidate_sha256") == dialogue_digest(training_record("multiturn", record)):
            check, path = review["verdict"], "review_repair.verdict"
    if check is None and record.get("reason") == "cot_native_review_rejected":
        native = record["native_generation"]
        original = record["messages"][-1]
        if (native.get("content_sha256") == dialogue_digest([original["reasoning_content"], original["content"]])
                and record.get("cot_review_policy", {}).get("semantic_status") == "not_accepted"):
            check, path = record.get("judge"), "judge"
    if isinstance(check, dict) and isinstance(check.get("reason"), str) and check["reason"].strip():
        findings.append({"quote": previous["content"], "issue": check["reason"],
            "source_ref": {"kind": "saved_model_review", "path": path,
                "candidate_sha256": dialogue_digest(record), "assessment_sha256": dialogue_digest(check)}})
    if instruction.strip():
        findings.append({"quote": previous["content"], "issue": instruction,
            "source_ref": {"kind": "human_revision_instruction", "path": "repair_input.instruction"}})
    return findings


def _recorded_feedback_author(record):
    """Authenticate the saved continuation without treating a display merge as wire."""
    native = record["native_generation"]
    receipt = native.get("review_guidance", native.get("tool_feedback"))
    try:
        authenticated = authenticate_native_feedback_receipt(receipt)
        session, revised = native["continuation_session"], native["revised_assistant"]
        reasoning, answer = authenticated["value"]["reasoning"], authenticated["value"]["answer"]
        if (session != authenticated["continuation_session"] or session != native["author_session"]
                or revised != authenticated["revised_assistant"]
                or native["response_details"] != receipt["response_details"]
                or record["reasoning"] != [reasoning] or record["answer"] != answer
                or record["messages"][-1]["reasoning_content"] != reasoning
                or record["messages"][-1]["content"] != answer
                or native["content_sha256"] != dialogue_digest([reasoning, answer])):
            raise ValueError
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        raise ValueError("native_dialogue_feedback_provenance_mismatch") from None
    return revised, session


def native_dialogue_progress_blocker(record, *, version=1, diagnostic=False):
    """Allow a narrow diagnostic continuation, never certify training quality.

    The scoped native check must already have located an unsupported private
    verbosity setting in reasoning only. Missing channels, unknown references,
    delivery errors and any additional issue remain stopping conditions.
    """
    if version == 2 and diagnostic:
        return _diagnostic_response_blocker(record)
    native = record.get("native_generation") or {}
    reasoning = record.get("reasoning")
    answer = record.get("answer")
    messages = record.get("messages")
    source = record.get("source_context") or {}
    provided = record.get("provided_source_context") or {}
    issues = native.get("issues")
    evidence = native.get("instruction_scope_evidence") or []
    blocked = [finding for finding in evidence if finding.get("disposition") == "block"]
    if (record.get("status") != "quarantined"
            or record.get("reason") != "native_generation_instruction_leakage"
            or issues != ["native_generation_instruction_leakage"]
            or native.get("instruction_scope_version") != 1
            or native.get("raw_reasoning_preserved") is not True
            or not isinstance(reasoning, list) or len(reasoning) != 1
            or text_issue(reasoning[0]) or text_issue(answer)
            or conversation_issue(messages)
            or messages[-1].get("content") != answer
            or messages[-1].get("reasoning_content") != reasoning[0]
            or native.get("content_sha256") != dialogue_digest([reasoning[0], answer])
            or source.get("kind") != "brief" or source.get("synthetic") is not True
            or source.get("human_provided") or source.get("source_context_pack") or source.get("tools")
            or provided.get("scope") != "learner_dialogue_only"
            or provided.get("private_scene") != "not_provided"
            or record.get("task_delivery", {}).get("observable_passed") is not True
            or not blocked or native.get("leakage_evidence") != blocked
            or any(message.get("role") == "tool" or message.get("tool_calls") or message.get("toolCalls")
                   for message in messages)):
        return None
    for finding in blocked:
        start, end = finding.get("reference_start"), finding.get("reference_end")
        if (finding.get("classification") != "unsupported_runtime_setting"
                or finding.get("field") != "reasoning"
                or finding.get("issue") != "native_generation_instruction_leakage"
                or type(start) is not int or type(end) is not int
                or not 0 <= start < end <= len(reasoning[0])
                or reasoning[0][start:end] != finding.get("reference")):
            return None
    return {"reason": "unsupported_private_reasoning_setting",
        "issues": deepcopy(issues), "evidence": deepcopy(blocked),
        "response_sha256": native["content_sha256"],
        "training_eligible": False, "semantic_status": "pending_ordinary_checks"}


# These are established training-quality outcomes, not transport/source/role
# failures. Unknown issue codes stop: a new integrity error cannot silently
# inherit permission to continue a diagnostic conversation.
_DIAGNOSTIC_QUALITY_ISSUES = frozenset({
    "native_generation_instruction_leakage", "native_instruction_reference_unresolved",
    "training_surface_internal_process_reference", "training_surface_hidden_source_reference",
    "training_surface_knowledge_source_narration", "cot_native_review_rejected",
})


def _diagnostic_response_blocker(record):
    """Separate a complete recorded response from its unchanged quality verdict.

    Only explicit diagnostic continuation of a synthetic, source-free scene
    uses this policy. Quality labels remain release blockers; this function
    does not reinterpret a phrase or overturn any existing checker.
    """
    native = record.get("native_generation") or {}
    reasoning, answer = record.get("reasoning"), record.get("answer")
    messages, prompt = record.get("messages"), record.get("question")
    source, provided = record.get("source_context") or {}, record.get("provided_source_context") or {}
    issues = native.get("issues")
    try:
        public_notes = authenticated_review_guidance_positions(record)
    except ValueError:
        return None
    if (record.get("status") != "quarantined" or record.get("reason") not in _DIAGNOSTIC_QUALITY_ISSUES
            or not isinstance(issues, list)
            or any(not isinstance(issue, str) or issue not in _DIAGNOSTIC_QUALITY_ISSUES for issue in issues)
            or not isinstance(reasoning, list) or len(reasoning) != 1
            or text_issue(reasoning[0]) or text_issue(answer)
            or completed_turn_ends(messages, minimum_turns=1, public_system_positions=public_notes)[1]
            or messages[-1].get("content") != answer
            or messages[-1].get("reasoning_content") != reasoning[0]
            or not isinstance(prompt, list) or prompt != without_source_reasoning(messages[:-1])
            or native.get("raw_reasoning_preserved") is not True
            or native.get("channels") != ["reasoning_content", "content"]
            or native.get("content_sha256") != dialogue_digest([reasoning[0], answer])
            or source.get("kind") != "brief" or source.get("synthetic") is not True
            or source.get("human_provided") or source.get("source_context_pack") or source.get("tools")
            or provided.get("scope") != "learner_dialogue_only" or provided.get("private_scene") != "not_provided"
            or provided.get("text_chars") != 0 or provided.get("text_sha256") != dialogue_digest("")
            or record.get("task_delivery", {}).get("observable_passed") is not True
            or any(message.get("role") == "tool" or message.get("tool_calls") or message.get("toolCalls")
                   for message in messages)):
        return None
    return {"reason": "native_quality_pending", "diagnostic_policy_version": 2,
        "issues": list(dict.fromkeys([*issues, record["reason"]])),
        "evidence": deepcopy(native.get("instruction_scope_evidence", native.get("leakage_evidence", []))),
        **({"judge": deepcopy(record["judge"])} if "judge" in record else {}),
        "response_sha256": native["content_sha256"],
        "training_eligible": False, "semantic_status": "pending_ordinary_checks"}
