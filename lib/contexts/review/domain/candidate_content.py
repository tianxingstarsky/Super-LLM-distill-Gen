"""Pure candidate edits, training-surface checks and reasoning provenance."""
from __future__ import annotations
from copy import deepcopy
import hashlib
from lib.domain.workflow_quality import canonical, conversation_issue, text_issue
from lib.contexts.workflows.contracts import (
    TRAINING_FIELDS, PREFERENCE_TARGETS, training_record, retire_imported_reasoning,
)
from lib.domain.math_tasks import validate_math_candidate
from lib.domain.source_quote import quote_spans
from lib.domain.corpus_quality import inspect_corpus
from lib.domain.authored_reasoning import AUTHORED_REASONING_VERSION
from lib.domain.training_surface import training_surface_issue
from .reasoning_history import retire_reasoning_prefill


def candidate_digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def preserve_provider_reasoning(target, original, updated, *, origin):
    """Retire native evidence when its task, answer, or reasoning is revised.

    A score alone does not change provenance. Revisions keep exact native text
    and its original answer in history, with a digest/reference to the old task.
    An unchanged native rationale is not carried onto a changed answer or user
    turn; a newly authored replacement can remain as revision text. Other
    unmodified turns keep their original provider provenance.
    Explicit import receipts become input-only after edits. Legacy rows without
    import receipts or provider reasoning/summary retain their existing schema.
    """
    retire_imported_reasoning(target, original, updated, origin=origin)
    retire_reasoning_prefill(target, original, updated, origin=origin)
    turn_reviews = original.get("turn_reviews", [])
    turn_reviews = turn_reviews if isinstance(turn_reviews, list) else []
    provider_turns = [review for review in turn_reviews if isinstance(review, dict) and (
        review.get("reasoning_origin") == "provider_reasoning" or review.get("reasoning_summary"))]
    if (original.get("reasoning_origin") != "provider_reasoning"
            and not original.get("reasoning_summary") and not provider_turns):
        return updated
    fields = TRAINING_FIELDS.get(target, ())
    before = {field: original[field] for field in fields if field in original}
    after = {field: updated[field] for field in fields if field in updated}
    before_body = canonical(before)
    if before_body == canonical(after):
        return updated
    before_hash = hashlib.sha256(before_body.encode("utf-8")).hexdigest()
    old_messages, new_messages = original.get("messages", []), updated.get("messages", [])
    entries = []
    metadata_fields = ("reasoning_origin", "reasoning_summary", "reasoning_api_format", "reasoning_call")

    def text_reasoning(value):
        if isinstance(value, str):
            return value
        if isinstance(value, list) and all(isinstance(part, str) for part in value):
            return "\n".join(value)
        return ""

    def message_reasoning(message):
        return next((message[field] for field in ("reasoning_content", "reasoning", "thinking")
                     if isinstance(message.get(field), str) and message[field]), "")

    def retire(metadata, answer, native, prompt, *, message_index=None):
        entry = {"version": 1, "scope": "historical_provider_response_only", "superseded_by": origin,
                 "target": target, "answer": deepcopy(answer),
                 "content_sha256": before_hash,
                 "prompt_sha256": hashlib.sha256(canonical(prompt).encode("utf-8")).hexdigest(),
                 **{field: deepcopy(metadata[field]) for field in metadata_fields if field in metadata}}
        if native:
            entry["reasoning_content"] = native
        if original.get("source_id") is not None:
            entry["source_id"] = original["source_id"]
        if message_index is not None:
            entry["end_message_index"] = message_index
        entries.append(entry)

    def mark_revision(metadata):
        metadata["reasoning_origin"] = origin
        for field in metadata_fields[1:]:
            metadata.pop(field, None)

    def remove_unchanged_native(message, native):
        if native and isinstance(message, dict) and message.get("role") == "assistant":
            for field in ("reasoning_content", "reasoning", "thinking"):
                if message.get(field) == native:
                    message.pop(field, None)

    if original.get("reasoning_origin") == "provider_reasoning" or original.get("reasoning_summary"):
        if target == "cot":
            native = (text_reasoning(original.get("reasoning"))
                      if original.get("reasoning_origin") == "provider_reasoning" else "")
            retire(original, original.get("answer", ""), native, original.get("question"))
            if native and text_reasoning(updated.get("reasoning")) == native:
                updated["reasoning"] = []
            if new_messages:
                remove_unchanged_native(new_messages[-1], native)
        else:
            index = next((index for index in range(len(old_messages) - 1, -1, -1)
                          if old_messages[index].get("role") == "assistant"), None)
            message = old_messages[index] if index is not None else {}
            native = message_reasoning(message) if original.get("reasoning_origin") == "provider_reasoning" else ""
            retire(original, message.get("content", ""), native,
                   old_messages[:index] if index is not None else [], message_index=index)
            if index is not None and index < len(new_messages):
                remove_unchanged_native(new_messages[index], native)
        mark_revision(updated)

    for review in provider_turns:
        index = review.get("end_message_index")
        if type(index) is not int or not 0 <= index < len(old_messages):
            continue
        old = old_messages[index]
        new = new_messages[index] if index < len(new_messages) else {}
        old_user = next((message for message in reversed(old_messages[:index]) if message.get("role") == "user"), None)
        new_user = next((message for message in reversed(new_messages[:index]) if message.get("role") == "user"), None)
        if canonical([old_user, old]) == canonical([new_user, new]):
            continue
        native = message_reasoning(old) if review.get("reasoning_origin") == "provider_reasoning" else ""
        retire(review, old.get("content", ""), native, old_user, message_index=index)
        remove_unchanged_native(new, native)
        for current in updated.get("turn_reviews", []):
            if isinstance(current, dict) and current.get("end_message_index") == index:
                mark_revision(current)
    if entries:
        history = updated.get("provider_reasoning_history", [])
        updated["provider_reasoning_history"] = [*deepcopy(history if isinstance(history, list) else []), *entries]
    return updated


def _authored_cot(target, row):
    return target == "cot" and row.get("reasoning_protocol_version") == AUTHORED_REASONING_VERSION


def _authored_review_audit(target, row):
    if not _authored_cot(target, row):
        return {}
    from lib.contexts.review.domain.candidate_content import candidate_digest as digest
    reasoning = row.get("reasoning")
    if isinstance(reasoning, list) and all(isinstance(part, str) for part in reasoning):
        reasoning = "\n".join(reasoning)
    current_hash = digest({"reasoning": reasoning, "answer": row.get("answer")})
    saved_hash = row.get("reasoning_trace_content_sha256")
    trace = row.get("reasoning_trace")
    status = row.get("reasoning_trace_status")
    current = (isinstance(trace, dict) and saved_hash == current_hash
               and not (isinstance(status, str) and status.startswith("superseded")))
    audit = {"reasoning_protocol_version": AUTHORED_REASONING_VERSION,
             "reasoning_trace_status": "current" if current else status or "content_changed",
             "reasoning_content_sha256": current_hash}
    if isinstance(trace, dict):
        audit["reasoning_trace" if current else "generation_trace"] = deepcopy(trace)
        audit["reasoning_trace_content_sha256" if current else "generation_trace_content_sha256"] = saved_hash
    if not current:
        audit["generation_trace_scope"] = "historical_generation_audit_only_not_current_proof"
    return audit


def _row_training_surface_issue(target, row, recipe=None):
    """Check exactly the versioned training surface, including native v2 text.

    Frozen v1 runs retain their former native-evidence exemption. New v2 runs
    inspect every exported assistant rationale, regardless of provenance.
    """
    if not isinstance(row, dict):
        return None
    if (row.get("training_surface_protocol_version") == 2
            or (recipe or {}).get("training_surface_protocol_version") == 2):
        from lib.domain.training_surface import training_record_surface_issue
        voice = {}
        if (recipe or {}).get("knowledge_application_voice_version") == 3:
            voice["knowledge_application_voice_version"] = 3
            if "learning_goal" in (recipe or {}).get("qa_director", {}):
                voice["learning_goal"] = recipe["qa_director"]["learning_goal"]
        return training_record_surface_issue(target, row, protocol_version=2, **voice)
    if target not in {"sft", "multiturn", "agent", "cot"}:
        return None
    authored_origins = {"prompt_styled_generation", "prompt_revision", "human_revision", "prompt_trim"}
    if (row.get("training_surface_protocol_version") != 1
            and (recipe or {}).get("training_surface_protocol_version") != 1):
        return None
    origin = row.get("reasoning_origin")
    authored = (origin in authored_origins or row.get("training_surface_protocol_version") == 1)
    authored = authored and origin != "provider_reasoning"
    contract = row.get("qa_contract")
    if target == "cot":
        question = row.get("question")
        visible = question if isinstance(question, list) else [{"role": "user", "content": question}]
        return training_surface_issue(row.get("reasoning") if authored else "", row.get("answer"),
                                      visible_messages=visible, contract=contract)
    if target in {"sft", "multiturn", "agent"}:
        messages = row.get("messages", [])
        if not isinstance(messages, list):
            return None
        reviews = row.get("turn_reviews", [])
        reviews = reviews if isinstance(reviews, list) else []
        native_turns = {review.get("end_message_index") for review in reviews
                        if isinstance(review, dict) and review.get("reasoning_origin") == "provider_reasoning"}
        # CoT rewrites the final response while its imported turn audit can
        # still describe the historical provider response that it replaced.
        final_index = next((index for index in range(len(messages) - 1, -1, -1)
                            if isinstance(messages[index], dict) and messages[index].get("role") == "assistant"), None)
        native_turns.discard(final_index)
        for index, message in enumerate(messages):
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            reasoning = (next((message.get(field) for field in ("reasoning_content", "reasoning", "thinking")
                               if message.get(field)), "") if authored and index not in native_turns else "")
            if issue := training_surface_issue(reasoning, message.get("content"),
                                                visible_messages=messages[:index], contract=contract):
                return issue
    return None


def _payload_issue(target, row):
    if target == "gsm8k" and not validate_math_candidate(row):
        return "gsm8k_arithmetic_verification_failed"
    if target == "cpt":
        quality = inspect_corpus(row.get("text"))
        return None if quality["keep"] else quality["reason"]
    if target in PREFERENCE_TARGETS:
        prompt, chosen, rejected = row.get("prompt"), row.get("chosen"), row.get("rejected")
        if not all(isinstance(value, list) for value in (prompt, chosen, rejected)):
            return "review_repair_invalid_preference"
        return conversation_issue([*prompt, *chosen]) or conversation_issue([*prompt, *rejected])
    try:
        payload = training_record(target, row)
    except (KeyError, TypeError, ValueError):
        return "review_repair_content_unavailable"
    if target in {"sft", "multiturn", "agent"}:
        return conversation_issue(payload["messages"]) or _row_training_surface_issue(target, row)
    if target == "cot":
        question = row.get("question")
        if not isinstance(question, (str, list)) or not question:
            return "review_repair_invalid_question"
        if isinstance(question, list) and (issue := conversation_issue(question, final=False)):
            return issue
        reasoning = row.get("reasoning")
        if isinstance(reasoning, list):
            reasoning = "\n".join(reasoning) if all(isinstance(part, str) for part in reasoning) else None
        return text_issue(row.get("answer")) or text_issue(reasoning) or _row_training_surface_issue(target, row)
    return next((text_issue(payload[field]) for field in TRAINING_FIELDS[target] if text_issue(payload[field])), None)


def _sync_cot_messages(row):
    reasoning = row.get("reasoning")
    if isinstance(reasoning, str):
        row["reasoning"] = [reasoning]
    question = row.get("question")
    if isinstance(question, (str, list)) and isinstance(row.get("answer"), str):
        prefix = question if isinstance(question, list) else [{"role": "user", "content": question}]
        values = row.get("reasoning")
        if isinstance(values, list) and all(isinstance(value, str) for value in values):
            row["messages"] = [*deepcopy(prefix), {"role": "assistant", "content": row["answer"],
                                                  "reasoning_content": "\n".join(values)}]
    return row


def _manual_patch(target, record, item):
    row = deepcopy(record)
    patch = item.get("corrected_record", {})
    if set(patch) - set(TRAINING_FIELDS[target]):
        raise ValueError("manual_review_patch_target_mismatch")
    row.update(deepcopy(patch))
    if target == "rlaif" and "responses" in patch:
        responses = patch["responses"]
        if (not isinstance(responses, list) or len(responses) != 2
                or any(not isinstance(response, dict) or not isinstance(response.get("response"), list) for response in responses)):
            raise ValueError("manual_review_patch_target_mismatch")
        ranked = sorted(responses, key=lambda response: response.get("preference_rank", 99))
        row["chosen"], row["rejected"] = deepcopy(ranked[0]["response"]), deepcopy(ranked[1]["response"])
        row.pop("responses", None)
        row.pop("criterion", None)
    if item.get("messages") is not None:
        if target not in {"sft", "multiturn"}:
            raise ValueError("manual_review_messages_target_mismatch")
        row["messages"] = deepcopy(item["messages"])
    if target in {"sft", "multiturn"}:
        messages = row.get("messages", [])
        if item.get("question"):
            first = next((message for message in messages if message.get("role") == "user"), None)
            if first is not None:
                first["content"] = item["question"]
        if item.get("answer"):
            last = next((message for message in reversed(messages) if message.get("role") == "assistant"), None)
            if last is not None:
                last["content"] = item["answer"]
    elif target == "cot":
        if item.get("question"):
            row["question"] = item["question"]
        if item.get("answer"):
            row["answer"] = item["answer"]
    elif item.get("question") or item.get("answer"):
        raise ValueError("manual_review_patch_target_mismatch")
    if _authored_cot(target, row) and canonical(
            {field: row.get(field) for field in TRAINING_FIELDS[target]}) != canonical(
            {field: record.get(field) for field in TRAINING_FIELDS[target]}):
        row["reasoning_trace_status"] = "superseded_by_human_edit"
    if target == "cot":
        _sync_cot_messages(row)
    return preserve_provider_reasoning(target, record, row, origin="human_revision")


def _immutable_tool_facts(row):
    return {"tools": row.get("tools", []), **{field: [message for message in row.get(field, [])
        if message.get("role") in {"system", "tool"} or message.get("tool_calls") or message.get("toolCalls")]
        for field in ("messages", "prompt", "chosen", "rejected", "question")
        if isinstance(row.get(field, []), list)}}


def _automatic_repair_task(target, row):
    """The delivered user task is fixed during an automatic answer repair.

    Human edits run through _manual_patch before this comparison and remain
    explicit task revisions. Assistant answers may change; actual user turns,
    message roles and teacher-independent input may not silently change.
    """
    if target == "cot":
        return row.get("question")
    if target in PREFERENCE_TARGETS:
        return row.get("prompt")
    if target in {"sft", "multiturn"}:
        messages = row.get("messages", [])
        return {"roles": [message.get("role") for message in messages],
                "input_messages": [message for message in messages if message.get("role") != "assistant"]}
    return None


def _selected_review_request(data, evidence):
    if evidence.get("source_line_scope") == "selected":
        return {**data, "source_line_scope": "selected",
                "source_line_ranges": deepcopy(evidence["source_line_ranges"])}
    return data


def _evidence_quote_spans(evidence, quotes):
    blocks = evidence.get("teacher_evidence_blocks")
    if blocks is None:
        return quote_spans(evidence["teacher_evidence"], quotes)
    if not isinstance(quotes, list) or not quotes:
        return None
    selected = evidence.get("source_line_scope") == "selected"
    ranges = evidence.get("source_line_ranges", [])
    source_ids = evidence.get("teacher_evidence_source_ids")
    if selected and len(ranges) != len(blocks):
        raise ValueError("invalid_source_line_ranges")
    if source_ids is not None and (not isinstance(source_ids, list) or len(source_ids) != len(blocks)):
        raise ValueError("human_session_source_evidence_unavailable")
    spans = []
    for index, quote in enumerate(quotes):
        for block_index, block in enumerate(blocks):
            matched = quote_spans(block, [quote])
            if matched is not None:
                spans.append({**matched[0], "quote_index": index,
                    "source_block_index": block_index,
                    "offset_scope": "selected_source_block" if selected else "verified_source_block",
                    **(ranges[block_index] if selected else {"source_id": (
                        source_ids[block_index] if source_ids is not None else evidence.get("source_id"))})})
                break
        else:
            return None
    return spans
