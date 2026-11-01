"""Use the existing native author at the actual SFT/dialogue answer boundary."""
from __future__ import annotations

from copy import deepcopy
import json

from .generate_native_cot import GenerateNativeCot
from ..domain.dialogue import dialogue_digest, without_source_reasoning
from ..domain.dialogue_context import project_answer_context
from ..domain.review_guidance import review_guidance_message


def generate_native_turn(port, recipe, *, stage, unit, messages, style,
                         source_text, source_scope, instruction="", identity=None, response_policy=None,
                         previous_candidate=None, revision_feedback=None, revision_session=None, revision_findings=None):
    """Generate one answer, retaining canonical history and provided-source evidence.

    The source receipt certifies which authenticated context was provided. It
    is not a model quotation, a claim-support verdict, or independent review.
    """
    scoped_source = {"kind": unit["kind"], "text": source_text,
                     "source_id": unit["source_id"]}
    sample = {**unit, "id": identity or unit["id"], "messages": deepcopy(messages),
              "source_context": scoped_source, "quotes": []}
    if previous_candidate is not None:
        sample["native_previous_candidate"] = deepcopy(previous_candidate)
        sample["native_revision_feedback"] = deepcopy(revision_feedback)
        if revision_session is not None:
            findings = deepcopy(revision_findings) if revision_findings is not None else [{
                "quote": previous_candidate["content"], "issue": revision_feedback,
                "source_ref": "authenticated_parent_revision_instruction"}]
            sample["native_tool_feedback"] = {"session": deepcopy(revision_session), "findings": findings}
    sample = project_answer_context(sample, unit,
        version=recipe.get("dialogue_context_scope_version"))
    if sample.get("answer_context_scope"):
        source_text = ""
        source_scope = sample["answer_context_scope"]["source_scope"]
    contract = sample.get("qa_contract", {})
    controls = {key: contract[key] for key in ("qa_type", "guidance", "learning_goal", "user_language", "answer_policy") if key in contract}
    # Request-only projection must not weaken local quality checks or replace
    # the original director contract retained in the candidate's audit trail.
    if "qa_contract" in unit:
        sample["qa_contract"] = deepcopy(unit["qa_contract"])
    if stage == "multiturn" and any(message.get("role") == "assistant" for message in messages):
        # The first design does not outrank actual follow-up information.
        for key in ("guidance", "user_language", "answer_policy"):
            controls.pop(key, None)
    if response_policy:
        controls["answer_policy"] = response_policy
    if controls:
        instruction += ("\n当前回答条件（不复述这些制作字段）：\n" + json.dumps(controls, ensure_ascii=False)
            + "\n仅凭实际证据回答；clarify 要针对缺失信息澄清，insufficient 要说明不能断言的边界，"
              "conditional 要保留必要条件，"
              "correct_premise 要纠正错误前提。后续问题以用户实际新增信息为准。")
    row, = GenerateNativeCot(port, recipe, stage=stage).execute(
        sample, style or {}, instruction=instruction)
    prompt = row.get("question", without_source_reasoning(messages))
    notes_receipt = row.get("native_generation", {}).get("review_guidance")
    note = (review_guidance_message(notes_receipt) if notes_receipt is not None
            and notes_receipt.get("status") == "completed" else None)
    # The only permitted suffix is the authenticated public system note.
    history_prompt = prompt[:-1] if note is not None and prompt[-1:] == [note] else prompt
    extra = len(history_prompt) - len(messages)
    preserved_history = deepcopy(messages)
    if (extra >= 0 and history_prompt[extra:] == without_source_reasoning(messages)
            and (note is None or prompt[-1:] == [note])):
        preserved_history = deepcopy(history_prompt[:extra]) + preserved_history
        if note is not None:
            preserved_history.append(deepcopy(note))
    else:
        row.update(port.reject(unit, "native_history_projection_mismatch"))
    reasoning = row.get("reasoning", [""])
    reasoning = reasoning[0] if isinstance(reasoning, list) and reasoning else ""
    row["messages"] = [*preserved_history, {"role": "assistant",
        "content": row.get("answer", ""), "reasoning_content": reasoning}]
    from ..domain.generation_control import generation_controls_metadata
    row.update(generation_controls_metadata([(len(row["messages"]) - 1, row)]))
    row["source_context"] = deepcopy(unit)
    row["provided_source_context"] = {
        "version": 1, "kind": "context_provided", "scope": source_scope,
        "text_sha256": dialogue_digest(source_text), "text_chars": len(source_text),
        "semantic_support": "not_assessed", "model_selected_quotes": False}
    if sample.get("answer_context_scope"):
        row["provided_source_context"]["private_scene"] = "not_provided"
    row["generation_check"] = "native_channels_and_scoped_source_context"
    row["evidence_level"] = ("learner_dialogue_only_not_independently_verified"
        if sample.get("answer_context_scope") else "source_context_provided_not_independently_verified")
    return row
