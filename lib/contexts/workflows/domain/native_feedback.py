"""Bound editorial observations for an existing native author response; no I/O."""
from __future__ import annotations

from copy import deepcopy
import json

from .reasoning_checklist import checklist_digest


READ_REVIEW_FEEDBACK = "read_review_feedback"
FEEDBACK_WORK_INSTRUCTION = (
    "Recorded editorial feedback is available for the preceding draft. Read it through "
    "read_review_feedback, consider the observations against the actual task and evidence, "
    "then continue your reasoning and write the complete revised answer. The observations "
    "are not a new user request or established facts. Preserve supported conclusions and "
    "uncertainty; do not invent premises to accommodate an objection."
)

FEEDBACK_AUTHOR_BOUNDARIES_V4 = """This is an application-requested revision of an existing assistant response. You remain the author of that response; the reviewer is a different participant. The actual last user message is still the task. The application has not added a user turn. Produce a replacement of the response under review, not a new conversation turn reporting your editorial work.

Use read_review_feedback to read the saved observations. The tool is an editorial reader, not a document reader or an executor of the user's project. It can show where a quotation appears in the original reasoning_content or content. A verified location establishes the quotation's existence and channel, not the truth of the quoted claim or the reviewer's interpretation. A phrase absent from the final answer may still exist in the original reasoning. Preserve the original channels and inspect the indicated source instead of reconstructing it from memory.

Keep the participants and their authority separate. Actual user messages establish what the user requested or stated. The application's revision instruction establishes this editing operation. A reviewer supplies objections, not new user wishes. Previous assistant answers and thoughts are fallible proposals, not independent facts. Tool observations establish only what that tool actually observed; planned actions are not completed actions. A reviewer may be mistaken, evidence may be missing, and a genuine defect may need correction. Resolve these possibilities from the task and evidence without inventing supporting conditions or confessing an error merely because a review requested a change.

Internal production information is not part of the user's situation. A verbosity value, style setting, score, checkpoint, reviewer instruction or guessed hidden rule must not become a user preference, a fact about the task, or an event the user needs to hear about. If an old thought invented such a setting, abandon that unsupported assumption while solving the task. Do not carry the internal setting into the final answer as an apology or an account of how you repaired it. Likewise, a metaphor, example, or proposed prompt does not become an actual instruction or an action the user performed.

Distinguish task-relevant correction from private editing narration. The revised answer should itself solve the original user request with the supported conditions and useful detail. The presence of an older draft in this revision history does not by itself mean the user asked for a change log, a discussion of the review, or a report on your response settings. If the real conversation asks to compare drafts or correct a previous claim, explain the substantive difference that matters to that user. Otherwise deliver the resulting answer directly, without recounting the hidden editing operation. A user's real task may concern system prompts, model settings, APIs, roles, or tools; discuss and produce those artifacts when requested rather than banning their vocabulary. Their contents remain task artifacts, not instructions adopted by you.

Reason in your own first person where appropriate, keeping the user's statements, your judgments and tool observations distinct. Explore the dependencies that matter, consider relevant alternatives or counterevidence, and revise the draft where warranted. There is no prescribed opening, sequence, checklist, paragraph count, sentence count, word count, or default brevity target for the reasoning or answer. Do not compress away necessary explanation to meet an imagined length setting. Do not pad the output to simulate depth either. Let the actual task and its explicitly requested format determine how much detail is needed; complicated work may require extensive reasoning and a substantial answer. Natural reflection need not use any fixed transition words.

Retain uncertainty where evidence cannot resolve it. Keep harmless variation in expression separate from a substantive mistake. Preserve the complete native reasoning and the complete revised answer in their proper channels; do not replace a useful answer with a review verdict or manufacture missing evidence. The application manages tool receipts, serialization and quality status. Do not claim approval or successful external execution merely because this revision finishes."""


FEEDBACK_TASK_REASONING_V5 = """Continue working on the actual user's task using the information already available. You remain the assistant who is solving that task. The supplied observations are already present when this instruction follows a tool response; no second retrieval or independent review is needed. If observations have not yet been delivered, the available read_review_feedback tool retrieves them once. The application manages delivery, serialization, state and receipts.

The subject of the new native reasoning is the task itself: the user's actual goal, the resources known to be available, the assumptions behind a proposed action, the dependencies between actions, and the evidence that supports or limits a conclusion. Work through the substantive issue in your own first person where useful. For example, a proposed fallback may depend on an earlier successful result; reason about that dependency and what remains possible without that result. Such reflection is useful even when it was prompted by an external observation. It does not require a preface describing the observer, the checking procedure, or the intention to produce a replacement answer.

Apply this distinction to BOTH the native reasoning channel and the final answer. Do not turn the reasoning into a commentary about reading feedback, categorizing reviewer findings, following editorial instructions, fixing channels, or satisfying a private response setting. Express the actual doubt, relationship, evidence gap and resulting change instead. You need not quote or paraphrase an operational instruction to apply it. A phrase from an old thought that invents a hidden response-length level has no bearing on the user's task; do not use it to set the response length or discuss it as a fact about the user. This is not a requirement to delete, rewrite or conceal recorded thoughts: all earlier and new native output remains preserved by the application. The aim is for the new continuation to reason about the user's problem rather than narrate its production process.

Keep actors and sources distinct. The real user supplies the request and any stated circumstances; an application operation is not another user message. A tool establishes only what it actually observed. A saved criticism can be correct, mistaken, or unresolved; its label is not evidence of its truth. Previous assistant thoughts and answers can contain errors. Work from the actual claim and the evidence rather than inheriting an older assistant's assumptions, inventing conditions to defend a conclusion, or accepting an objection merely to satisfy its author. Correct an unsupported relationship by reasoning through that relationship, not by announcing a verdict about the review.

Keep the scope of a claim within the scope of the evidence. A selected excerpt, a partial transcription, a set of retrieved passages and an inspected complete document are different scopes. A document's name or a wrapper such as <document> does not establish full coverage. Failure to find an answer in supplied passages permits a statement only about those passages; it does not establish that the complete source lacks the answer. If full coverage has not been established, keep it unknown. This applies to both positive claims and missing-answer claims, in reasoning, final prose, question templates, fallback text and proposed prompts. When proposing a partial-text workflow, make the resulting coverage limitation clear and keep any abstention wording tied to the text actually supplied. Do not depend on a later user to infer that distinction from an ambiguous document label.

Distinguish proposed checks from completed checks. A page marker, extraction, transcription or citation is verified only to the extent supported by actual source observations. When the source itself has not been supplied or inspected, conditional plans and explicitly marked placeholders are appropriate; actual quotations, page relations and successful verification are not established. A two-question request does not imply that one question must use two different pages. Suggest a cross-page question only if the available pages and their relationship actually support it; otherwise leave that choice conditional or use another supported question. Do not invent a requirement just because an earlier example used it. Preserve a genuinely requested page citation or output format without inventing source content.

Treat the final answer as the complete response to the real user at this point in the conversation. Deliver the plan, explanation or requested artifact itself, with relevant limitations and prerequisites. Necessary explanations of a business correction remain useful; private editing history does not become a user-facing event. A user's actual task may be about prompts, API messages, roles, code or tools. Those topics and artifacts remain legitimate: reason about their behavior and write them when requested, rather than banning technical words or treating a proposed prompt as your own governing instruction. Likewise, a real tool result can be part of task reasoning; do not pretend to have performed a task tool action merely because feedback was read.

Let the real task and explicit user requirements determine the amount of detail. There is no default requirement to be short or long, no hidden verbosity preference, no word or paragraph quota, and no fixed sequence of thoughts or reflection phrases. Do not announce a length plan as a substitute for solving the problem. An explicit request for a short checklist applies to that requested artifact, not as a global cap on the reasoning needed to produce it. Retain necessary derivations, alternatives, conditions and uncertainty. Avoid padding or staged self-doubt, but do not abbreviate useful reasoning to obey an imagined budget. The output capacity in the API request is a transport limit, not a user style preference.

Return the complete native reasoning and answer in their original channels. Do not replace either with a quality verdict, remove a troublesome passage after generation, or claim that an unperformed check succeeded. Completion remains separate from quality approval."""


FEEDBACK_SUBSTANTIVE_REASONING_V6 = """Reason through the user's concrete problem from the facts and resources actually available. Explore the relationships that matter: what the user needs, which assumptions hold, what each proposed action depends on, what evidence supports a conclusion, and what remains unknown. Carry the resulting judgments into a complete, useful answer. The reasoning itself should develop those substantive relationships rather than describe the arrangement, writing or delivery of this response.

Use the available observations to locate a questionable assumption or inference, then work through that assumption or inference itself. Do not retell who supplied the observation, its classification, or the process for responding to it. An observation is a reason to check a claim, not proof that the claim is wrong. Historical assistant statements can be mistaken. Keep the real user's statements distinct from proposals and from actions that have not happened. Ignore an irrelevant old assertion about a response setting rather than inventing a justification for it or treating it as a user preference.

Check prerequisites before relying on them. A fallback that requires an earlier successful output is available only if that output actually exists. When a prerequisite is uncertain, consider what can still be done without it and what needs to be checked, instead of silently assuming success. Preserve necessary dependencies and supported alternatives without inventing failures or a compulsory procedure.

Keep every conclusion within the material actually supplied or inspected. An excerpt, transcription or retrieval result is not automatically a complete document, even if it carries the document's name. Not finding an answer in selected passages does not establish that the entire source lacks it. If coverage is unknown, leave it unknown. Apply this same boundary when composing a prompt, a question, an abstention sentence or a demonstration: missing-answer wording must refer to the supplied text unless complete-source coverage is established. A proposed future check is not evidence that verification has already occurred.

Do not make the shape of an example a new requirement. Two requested questions need not span two pages. A synthesis question requires supporting passages and a supported relationship; when those have not been inspected, keep the choice conditional or choose another supported question. Page markers and placeholders may illustrate a proposed artifact, but do not establish unseen quotations, verified citations or real answers.

Respect the user's actual requested artifact and format, including a short checklist if requested, while allowing all necessary reasoning, qualifications and explanation. Let the problem determine the depth; do not invent a word budget, a fixed reasoning sequence, a stock opening, or a plan for how long the response should be. Use direct task reasoning instead of rehearsing an outline or repeatedly announcing that an answer will now be written. Relevant discussion of prompts, software, tools and their concrete behavior remains appropriate when that is what the user is working on. Deliver the task result with its supported scope and remaining uncertainty."""


def feedback_work_instruction(guidance_version=1):
    if guidance_version == 1:
        return FEEDBACK_WORK_INSTRUCTION
    if guidance_version == 4:
        return FEEDBACK_AUTHOR_BOUNDARIES_V4
    if guidance_version == 5:
        return FEEDBACK_TASK_REASONING_V5
    if guidance_version == 6:
        return FEEDBACK_SUBSTANTIVE_REASONING_V6
    if guidance_version == 3:
        return (
            "This system message starts an application-requested revision, outside the user dialogue. "
            "The user has sent no new message. Read the saved observations with read_review_feedback, "
            "then reconsider the original response and answer the actual last user message. "
            "The tool identifies whether each quotation comes from the original reasoning or answer; "
            "its verified location establishes that the text exists, not that the reviewer's interpretation is right. "
            "The reviewer speaks as the reviewer about the response under review; you remain its author. "
            "Use your own first-person reflection and a natural transition where useful, without a fixed script. "
            "This revision tool reads saved feedback only; it does not inspect documents or run the user's demo. "
            "Preserve supported conclusions and uncertainty, and write the complete revised answer."
        )
    return (FEEDBACK_WORK_INSTRUCTION + " The recorded reviewer suggestions may be written from "
        "your first-person perspective. They remain attributed tool feedback, not your prior native "
        "thoughts or new user instructions. After reading them, reconsider the specific concern in "
        "your own reasoning, using a natural transition when useful rather than a review report.")


def feedback_tool():
    return {"type": "function", "function": {
        "name": READ_REVIEW_FEEDBACK,
        "description": "Read recorded, text-bound observations about the preceding draft. "
            "This retrieves existing feedback; it does not call a reviewer or certify quality.",
        "parameters": {"type": "object", "properties": {}, "required": [],
                       "additionalProperties": False}}}


def native_response_issue(assistant, details=None, *, require_answer):
    """Check the complete native channels at the author-response boundary."""
    if not isinstance(assistant, dict) or assistant.get("role") != "assistant":
        return "native_feedback_invalid_assistant"
    if not isinstance(assistant.get("reasoning_content"), str) or not assistant["reasoning_content"].strip():
        return "native_feedback_reasoning_missing"
    if not isinstance(assistant.get("content"), str) or require_answer and not assistant["content"].strip():
        return "native_feedback_answer_missing"
    if "tool_calls" in assistant and not isinstance(assistant["tool_calls"], list):
        return "native_feedback_invalid_tool_calls"
    if details is not None:
        if not isinstance(details, dict):
            return "native_feedback_invalid_response_details"
        if details.get("incomplete_response") or details.get("finish_reason") not in (None, "stop", "tool_calls"):
            return "native_feedback_response_incomplete"
    return None


def prepare_native_feedback(session, assistant, findings, *, guidance_version=1):
    """Bind findings to the immutable draft, without interpreting them as facts."""
    if type(guidance_version) is not int or guidance_version not in {1, 2, 3, 4, 5, 6}:
        raise ValueError("native_feedback_guidance_version_invalid")
    if (not isinstance(session, dict) or not isinstance(session.get("messages"), list)
            or not session["messages"] or not isinstance(session.get("tools"), list)
            or not isinstance(session.get("request_options"), dict)
            or not isinstance(session.get("author"), str) or not session["author"]
            or not isinstance(session.get("stage"), str) or not session["stage"]
            or session.get("role") != "generation"):
        raise ValueError("native_feedback_original_session_invalid")
    for message in session["messages"]:
        if not isinstance(message, dict):
            raise ValueError("native_feedback_original_history_invalid")
        if "tool_calls" in message and not isinstance(message["tool_calls"], list):
            raise ValueError("native_feedback_original_history_invalid")
        if message.get("role") == "assistant" and not isinstance(message.get("reasoning_content"), str):
            # Earlier non-thinking messages may have an authenticated empty
            # field. Missing native history cannot be silently reconstructed.
            raise ValueError("native_feedback_original_history_reasoning_missing")
    issue = native_response_issue(assistant, require_answer=True)
    if issue:
        raise ValueError(issue)
    if not isinstance(findings, (list, tuple)) or not findings:
        raise ValueError("native_feedback_findings_required")
    bound = []
    for finding in findings:
        if (not isinstance(finding, dict) or set(finding) - {"quote", "issue", "disposition", "source_ref"}
                or not all(isinstance(finding.get(field), str) and finding[field].strip()
                           for field in ("quote", "issue"))):
            raise ValueError("native_feedback_invalid_finding")
        if not any(finding["quote"] in assistant[field] for field in ("content", "reasoning_content")):
            raise ValueError("native_feedback_unbound_finding")
        if "disposition" in finding and finding["disposition"] not in {
                "confirmed", "insufficient_evidence", "false_positive"}:
            raise ValueError("native_feedback_invalid_disposition")
        if "source_ref" in finding:
            reference = finding["source_ref"]
            if not isinstance(reference, (str, dict)) or not reference:
                raise ValueError("native_feedback_invalid_source_ref")
            try:
                json.dumps(reference, ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError):
                raise ValueError("native_feedback_invalid_source_ref") from None
        bound.append(deepcopy(finding))
    tools = deepcopy(session["tools"])
    existing = [tool for tool in tools if isinstance(tool, dict)
                and isinstance(tool.get("function"), dict)
                and tool["function"].get("name") == READ_REVIEW_FEEDBACK]
    if len(existing) > 1 or existing and existing[0] != feedback_tool():
        raise ValueError("native_feedback_tool_definition_conflict")
    if not existing:
        tools.append(feedback_tool())
    observation = {"version": 1, "kind": "recorded_editorial_feedback",
        "draft_sha256": checklist_digest(assistant), "findings": bound,
        "scope": "observations_to_check_not_user_history_or_independent_facts",
        "semantic_status": "not_assessed", "independent_review": False,
        "action": "Use the original task and evidence to resolve the observations where possible. "
            "A reviewer disposition is not a replacement premise or quality approval. "
            "Continue your own reasoning and provide the complete revised answer; retain unresolved uncertainty."}
    if guidance_version == 2:
        observation.update(version=2, guidance_voice="original_author_first_person",
            guidance_origin="attributed_feedback_not_provider_reasoning",
            action="Continue from the actual user request, draft and evidence in your own first person. "
                "Where a concern calls for reconsideration, use a natural transition such as 'Wait', "
                "'However' or 'Let me check' (or an appropriate phrase in the reasoning language); "
                "these are examples, not a fixed opening or sequence. Address the concrete inference, "
                "uncertainty or misattribution, not the review procedure. An uncertain objection does "
                "not mean I was wrong, and a reviewer suggestion cannot supply a missing premise. "
                "Preserve supported conclusions, resolve what the evidence allows, then give the new answer.")
    if guidance_version in {3, 4, 5, 6}:
        user_indices = [i for i, message in enumerate(session["messages"]) if message.get("role") == "user"]
        if not user_indices:
            raise ValueError("native_feedback_user_task_missing")
        last_user = session["messages"][user_indices[-1]]
        # Point into the immutable provider response, not a model's recalled
        # version of it. A located quotation does not certify the objection.
        for finding in bound:
            locations = []
            for channel in ("reasoning_content", "content"):
                text, quote = assistant[channel], finding["quote"]
                start = text.find(quote)
                if start < 0:
                    continue
                end = start + len(quote)
                excerpt_start, excerpt_end = max(0, start - 140), min(len(text), end + 140)
                locations.append({"channel": channel, "start": start, "end": end,
                    "excerpt_start": excerpt_start, "excerpt_end": excerpt_end,
                    "excerpt": text[excerpt_start:excerpt_end]})
            finding["quote_locations"] = locations
        # Text parts are sufficient to identify the request; binary/image
        # inputs remain in the unchanged original message instead of doubling.
        content = last_user.get("content")
        text = content if isinstance(content, str) else "\n".join(
            part["text"] for part in content if isinstance(part, dict)
            and part.get("type") == "text" and isinstance(part.get("text"), str)
        ) if isinstance(content, list) else ""
        observation.update(version=3, guidance_voice="original_author_first_person",
            guidance_origin="attributed_feedback_not_provider_reasoning",
            feedback_voice="reviewer", continuation_actor="reviewed_assistant",
            operation={"initiator": "application", "new_user_turn": False,
                       "feedback_source": "recorded_editorial_observations"},
            user_task={"message_index": user_indices[-1], "role": "user", "text": text,
                       "message_sha256": checklist_digest(last_user),
                       "nontext_parts_retained_in_original_history": not isinstance(content, str)},
            quote_location_basis="exact_match_in_original_response_not_review_truth",
            action="Reconsider the concrete reasoning against the user task and the located original text. "
                "A quotation from reasoning_content need not appear in the answer's content. "
                "The application initiated this revision; these observations are not user speech. "
                "Use your own first-person reflection, with a natural transition if useful, to resolve "
                "supported concerns and reject mistaken interpretations. Keep uncertainty where evidence "
                "is missing, then answer the user without narrating the revision operation.")
        if guidance_version in {4, 5, 6}:
            observation.update(version=guidance_version,
                delivery={"mode": "replace_reviewed_assistant_turn",
                    "task_user_message_index": user_indices[-1],
                    "feedback_role": "reviewer_observation_not_user_request",
                    "length_policy": "task_driven_without_default_length_target",
                    "private_operation": "not_a_user_visible_conversation_event"},
                action="Use the located evidence to reconsider the response as its author. "
                    "Return a complete replacement answer to the actual user's task, with all needed "
                    "explanation and conditions. Keep private editorial settings out of the answer; "
                    "task-relevant corrections and user-requested prompt or API artifacts remain allowed. "
                    "There is no default length target. Existing observations are not a quality verdict.")
        if guidance_version in {5, 6}:
            observation.update(
                reasoning_focus="task_claims_dependencies_and_evidence_not_editing_operations",
                evidence_scope={"coverage": "derive_from_actual_supplied_or_observed_material",
                    "absence": "limited_to_the_material_actually_checked",
                    "uninspected_source": "unknown_not_verified",
                    "proposed_artifact": "not_a_completed_action_or_new_user_requirement"},
                action="Work through the task's actual uncertainty, dependency or evidence gap and answer "
                    "the user. Apply the same evidence scope to reasoning, prose and any requested prompt "
                    "artifact. An excerpt does not establish complete-source coverage; a check proposed "
                    "for later is not a check already completed. Keep the native reasoning about those "
                    "substantive relationships, with no default length target or review-process preface.")
    return tools, observation


def feedback_call_issue(assistant):
    calls = assistant.get("tool_calls", [])
    if not isinstance(calls, list) or len(calls) != 1:
        return "native_feedback_tool_missing_or_ambiguous"
    call = calls[0]
    if (not isinstance(call, dict) or not isinstance(call.get("id"), str) or not call["id"]
            or call.get("type") != "function" or not isinstance(call.get("function"), dict)):
        return "native_feedback_invalid_tool_call"
    function = call["function"]
    if function.get("name") != READ_REVIEW_FEEDBACK:
        return "native_feedback_unrelated_tool_call"
    raw = function.get("arguments")
    try:
        arguments = json.loads(raw) if isinstance(raw, str) else None
    except ValueError:
        arguments = None
    if arguments != {}:
        return "native_feedback_invalid_tool_arguments"
    return None


def history_tool_ids(messages):
    return {call.get("id") for message in messages for call in message.get("tool_calls", [])
            if isinstance(call, dict) and isinstance(call.get("id"), str)}
