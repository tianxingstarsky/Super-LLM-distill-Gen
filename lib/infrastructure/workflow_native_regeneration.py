"""Authenticated same-task regeneration through a new native reasoning prefix.

This opt-in repair route never uses the old answer as a seed or edits a saved
provider receipt. All paid phases use the ordinary Workflow.ask/checkpoints.
"""
from copy import deepcopy
import hashlib

from lib.domain.authored_reasoning import authored_rationale_issue, AUTHORED_REASONING_VERSION
from lib.domain.review_repair import validate_review_repair
from lib.domain.training_surface import training_surface_issue
from lib.domain.workflow_quality import accepted, conversation_issue, text_issue
from lib.domain.style_judge import style_verdict
from lib.domain.review_context import project_review_request
from lib.model_request_reliability import ModelRequestError


def _adherence(workflow, key, prompt_id, data):
    value = workflow.ask(key, "jev", prompt_id, project_review_request(workflow.recipe, data, phase="review"),
                         model_stage="review", prompt_stage="cot",
                         allow_reasoning_fallback=False)
    try:
        return style_verdict(value, maximum_reason_length=4000, inspect_reason=True)
    except ValueError:
        workflow.invalidate_checkpoint(["call", key])
        raise


def _empty_visible(error):
    return ((type(error) is ValueError and str(error) == "model_visible_output_missing")
            or isinstance(error, ModelRequestError) and error.kind == "invalid"
            and error.code == "model_visible_output_missing")


def regenerate_native_cot_item(workflow, item):
    """Rebuild one sealed parent's exact question without director or SFT calls."""
    from lib.infrastructure.training_workflow import digest
    from lib.infrastructure.workflow_review_repair import preserve_provider_reasoning
    from lib.model_reasoning_prefill import validate_prefill_client

    config = validate_review_repair(workflow.recipe.get("review_repair"))
    if (item.get("target") != "cot" or not config or config.get("native_regeneration") is not True
            or config["mode"] != "auto" or not getattr(workflow, "_authenticated_repair_branch", False)
            or workflow.recipe.get("native_regeneration_version") != 1
            or not isinstance(item.get("revision_context"), dict)
            or item["revision_context"].get("native_regeneration") is not True
            or any(name in item for name in ("patch", "corrected_record", "messages", "question",
                                            "answer", "reasoning", "score", "approved"))):
        raise ValueError("native_regeneration_authenticated_parent_required")
    original = deepcopy(item["record"])
    question = deepcopy(original.get("question"))
    if isinstance(question, str):
        actual = [{"role": "user", "content": question}]
    elif isinstance(question, list):
        actual = deepcopy(question)
    else:
        raise ValueError("native_regeneration_original_question_required")
    if not actual or conversation_issue(actual, final=False):
        raise ValueError("native_regeneration_original_question_invalid")
    if any(message.get("role") == "tool" or message.get("tool_calls") or message.get("function_call")
           for message in actual):
        raise ValueError("model_reasoning_prefill_tools_unsupported")
    validate_prefill_client(workflow.client("generation", stage="cot"))
    row = deepcopy(original)
    revision = deepcopy(item["revision_context"])
    row.update(id=digest([workflow.state["id"], "cot", revision["candidate_id"]]),
               revision_context=revision, question=question)
    if row.get("qa_contract"):
        row["qa_contract"] = deepcopy(row["qa_contract"])
        row["qa_contract"]["revision_context"] = deepcopy(revision)
    # Old content is retained for audit only, including when a new prefix is
    # rejected before any answer exists. It is never a current native result.
    previous = {"version": 1, "scope": "historical_parent_native_response_only",
        "parent_run_id": revision["parent_run_id"], "parent_candidate_id": revision["candidate_id"],
        "parent_record_sha256": digest(original),
        **{name: deepcopy(original[name]) for name in (
            "question", "reasoning", "answer", "messages", "reasoning_origin",
            "reasoning_summary", "reasoning_api_format", "reasoning_call",
            "reasoning_prefill", "answer_response_details", "prefix_review") if name in original}}
    row["native_regeneration_history"] = [*deepcopy(original.get("native_regeneration_history", [])), previous]
    evidence = workflow._review_evidence(row)
    if not isinstance(evidence, dict):
        raise ValueError("review_repair_source_evidence_unavailable")
    style = workflow.generation_style("cot", original["id"])
    if not style or not isinstance(style.get("instruction"), str) or not style["instruction"].strip():
        raise ValueError("native_regeneration_style_required")
    instruction = item.get("instruction", "")
    if (not isinstance(instruction, str) or len(instruction) > 6000
            or instruction and text_issue(instruction)):
        raise ValueError("invalid_review_repair_inputs")
    feedback = {"scope": "editorial_correction_not_user_history_or_independent_facts",
                "instruction": instruction}
    # The authenticated loader owns the source bodies. source contains only a
    # description so an old messages/reference answer cannot enter either phase.
    task = {"prompt": actual, "learner_messages": actual, "source_evidence": evidence,
            "source": {"source_id": evidence.get("source_id"), "kind": evidence["source_kind"]},
            "qa_contract": deepcopy(original.get("qa_contract")), "generation_style": style}
    history = []
    terminal, check, style_check = None, None, None
    pending_review = False
    attempts = config["max_rounds"]
    for attempt in range(attempts):
        workflow.check_cancel()
        key = [row["id"], "native_same_task_v1", attempt]
        workflow.state["stages"]["review"]["phase"] = "native_rationale"
        workflow.save(force=False)
        rationale = workflow.ask([*key, "rationale"], "generation", "workflow.cot_rationale",
            {**task, "feedback": feedback}, model_stage="cot", prompt_stage="cot",
            instruction="生成风格要求：\n" + style["instruction"], allow_reasoning_fallback=False)
        issue = authored_rationale_issue(rationale)
        if issue is None:
            issue = training_surface_issue(rationale["reasoning"], "", visible_messages=actual,
                                           contract=original.get("qa_contract"))
        if issue:
            history.append({"attempt": attempt, "phase": "rationale", "reason": issue})
            feedback = {"scope": "editorial_correction_not_user_history_or_independent_facts",
                        "instruction": instruction, "reasoning_issue": issue}
            continue
        checked = _adherence(workflow, [*key, "prefix_check"], "workflow.cot_prefix_check",
            {**task, "reasoning": rationale["reasoning"], "reasoning_trace": rationale["reasoning_trace"]})
        if not checked["keep"] or checked["adherence"] < 4:
            history.append({"attempt": attempt, "phase": "prefix", "verdict": checked})
            feedback = {"scope": "editorial_correction_not_user_history_or_independent_facts",
                        "instruction": instruction, "prefix_review": checked["reason"]}
            continue
        prefix = rationale["reasoning"]
        prefix_sha = hashlib.sha256(prefix.encode("utf-8")).hexdigest()
        answer_task = {name: deepcopy(value) for name, value in task.items() if name != "generation_style"}
        # Preserve editorial instructions privately, not as a fabricated user.
        answer_task["feedback"] = deepcopy(feedback)
        answer, empty = None, []
        for answer_attempt in range(2):
            answer_key = [*key, "native_answer", answer_attempt]
            empty_key = [*key, "empty_visible", answer_attempt, prefix_sha]
            expected = {"version": 1, "status": "empty_visible_output", "eligible": False,
                "code": "model_visible_output_missing", "answer_attempt": answer_attempt + 1,
                "prefix_sha256": prefix_sha, "answer_checkpoint_sha256": digest(["call", answer_key])}
            if workflow.checkpoint_has(empty_key):
                if workflow.checkpoint(empty_key, lambda: None) != expected:
                    raise ValueError("checkpoint_integrity_error")
                empty.append(expected)
                continue
            try:
                answer = workflow.ask(answer_key, "generation", "workflow.cot_native_answer", answer_task,
                    model_stage="cot", prompt_stage="cot", allow_reasoning_fallback=False,
                    reasoning_prefix=prefix, capture_response_details=True,
                    **({"native_request_attempts": 1} if answer_attempt else {}))
                break
            except (ValueError, ModelRequestError) as error:
                if not _empty_visible(error):
                    raise
                workflow.checkpoint(empty_key, lambda: expected)
                workflow.event("native_answer_empty", role="generation", **expected)
                empty.append(expected)
        if answer is None:
            terminal = {"version": 1, "code": "model_visible_output_missing", "answer_attempts": 2,
                "prefix_sha256": prefix_sha, "empty_response_receipts": empty,
                "rationale_checkpoint": {"stage": workflow.stage, "sha256": digest(["call", [*key, "rationale"]])},
                "prefix_review_checkpoint": {"stage": workflow.stage, "sha256": digest(["call", [*key, "prefix_check"]])}}
            history.append({"attempt": attempt, "phase": "native_answer", "reason": terminal["code"]})
            break  # Never buy another rationale for an empty visible answer.
        if not isinstance(answer, dict) or set(answer) != {"answer"} or text_issue(answer.get("answer")):
            raise ValueError("native_prefill_invalid_answer")
        details = deepcopy(getattr(answer, "response_details", {}))
        receipt = details.get("reasoning_prefill")
        if (not isinstance(receipt, dict) or receipt.get("transport") != "deepseek_chat_beta"
                or receipt.get("prefix_sha256") != prefix_sha):
            raise ValueError("native_regeneration_receipt_missing_or_mismatched")
        updated = {**deepcopy(row), "question": deepcopy(question), "reasoning": [prefix], "answer": answer["answer"]}
        preserve_provider_reasoning("cot", original, updated, origin="native_same_task_regeneration")
        # The newly paid native response owns current provenance, including
        # when its training text happens to match the old provider's bytes.
        for historical in ("reasoning_summary", "reasoning_api_format", "reasoning_call"):
            updated.pop(historical, None)
        updated.update(reasoning=[prefix], answer=answer["answer"],
            reasoning_origin="authored_prefix_native_answer", reasoning_protocol_version=AUTHORED_REASONING_VERSION,
            training_surface_protocol_version=1, reasoning_trace=deepcopy(rationale["reasoning_trace"]),
            reasoning_trace_content_sha256=digest({"reasoning": prefix, "answer": answer["answer"]}),
            reasoning_trace_status="current", reasoning_prefill=deepcopy(receipt), prefix_review=checked,
            reasoning_scope="authored_prefix_not_complete_provider_internal_reasoning", answer_response_details=details,
            generation_style=style, source_verification="authenticated_source_not_independent_fact_proof")
        updated["messages"] = [*deepcopy(actual), {"role": "assistant", "content": answer["answer"], "reasoning_content": prefix}]
        for stale in ("reason", "feedback", "native_answer_terminal", "native_answer_retry", "judge", "style_check"):
            updated.pop(stale, None)
        if empty:
            updated["native_answer_retry"] = {"version": 1, "answer_attempts": len(empty) + 1,
                                             "empty_response_receipts": empty}
        updated["native_regeneration"] = {"version": 1, "parent_run_id": revision["parent_run_id"],
            "parent_candidate_id": revision["candidate_id"], "parent_record_sha256": digest(original),
            "rationale_checkpoint_sha256": digest(["call", [*key, "rationale"]]),
            "prefix_review_checkpoint_sha256": digest(["call", [*key, "prefix_check"]])}
        check = workflow._review_score("cot", updated, evidence, [*key, "final"], instruction)
        if (workflow.recipe.get("claim_review_condition_contract_version") == 1
                and any(entry.get("review", {}).get("condition_guard", {}).get("needs_review")
                    for entry in updated.get("claim_review", {}).get("assessments", []))):
            # A support gap does not justify buying another rationale or style
            # judgment. This complete native body remains available to review;
            # phase=final also prevents incomplete-output cleanup below.
            history.append({"attempt": attempt, "phase": "final", "status": "waiting_manual_review",
                            "verdict": deepcopy(check)})
            row = updated
            row.update(status="quarantined", reason="condition_support_pending_review")
            pending_review = True
            break
        style_check = _adherence(workflow, [*key, "style"], "workflow.style_check",
            {"generation_style": style, "reasoning": prefix, "answer": answer["answer"],
             "prompt": deepcopy(actual), "learner_messages": deepcopy(actual)})
        score = min(check["scores"].values()) / 5
        keep = (accepted(check) and score >= config["score_threshold"]
                and style_check["keep"] and style_check["adherence"] >= 4)
        history.append({"attempt": attempt, "phase": "final", "verdict": deepcopy(check), "style": style_check})
        row = updated
        if keep:
            row.update(status="eligible", judge=deepcopy(check), style_check=style_check)
            break
        row.update(status="quarantined", reason="native_regeneration_quality_rejected")
        feedback = {"scope": "editorial_correction_not_user_history_or_independent_facts", "instruction": instruction,
                    "quality": check["reason"], "style": style_check["reason"]}
    else:
        row.update(status="quarantined", reason="native_regeneration_failed_after_review")
    incomplete = terminal is not None or not history or history[-1]["phase"] != "final"
    if terminal is not None:
        row.update(status="quarantined", reason="native_answer_missing_after_retry", native_answer_terminal=terminal,
                   reasoning_origin="native_regeneration_no_complete_answer")
    elif incomplete:
        row.update(status="quarantined", reason="native_regeneration_failed_after_prefix_review",
                   reasoning_origin="native_regeneration_no_complete_answer")
    if incomplete:
        for field in ("reasoning", "answer", "messages", "reasoning_prefill", "answer_response_details", "prefix_review",
                      "reasoning_trace", "reasoning_trace_status", "reasoning_trace_content_sha256", "reasoning_scope",
                      "reasoning_summary", "reasoning_api_format", "reasoning_call", "judge", "style_check"):
            row.pop(field, None)
    candidate_sha = (digest(workflow._review_payload("cot", row))
                     if row.get("answer") and row.get("reasoning") else None)
    row["review_repair"] = {"mode": "auto", "native_regeneration": True,
        "status": ("waiting_manual_review" if pending_review else
                   "accepted" if row.get("status") == "eligible" else "rejected"), "node": "review",
        "score": min(check["scores"].values()) / 5 if check else None, "score_threshold": config["score_threshold"],
        "attempts": len({entry["attempt"] for entry in history}), "history": history,
        "candidate_sha256": candidate_sha, "config_sha256": digest(config)}
    if pending_review:
        row["review_repair"].update(reason="condition_support_pending_review",
                                   stop_reason="condition_support_pending_review")
    row["_review_target"] = "cot"
    return [row]
