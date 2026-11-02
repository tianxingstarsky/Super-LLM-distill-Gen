"""Immutable learner task and evidence binding for a new SFT answer version."""
from copy import deepcopy

from .dialogue import dialogue_digest as digest
from .public_experience import without_injected_public_experience
from lib.domain.agent_experience import validate_agent_experience


def selected_reanswer_ids(candidate_ids):
    if (not isinstance(candidate_ids, list) or not 1 <= len(candidate_ids) <= 200
            or any(not isinstance(identifier, str) or not identifier for identifier in candidate_ids)
            or len(set(candidate_ids)) != len(candidate_ids)):
        raise ValueError("invalid_sft_reanswer_selection")
    return list(candidate_ids)


def reanswer_options(parent, selected, agent_experience):
    options = {key: deepcopy(parent[key]) for key in (
        "backend", "model", "judge_backend", "judge_model", "jev_backend", "jev_model",
        "node_models", "node_generation", "qa_director", "sft_output_style", "node_control_text_configs",
        "node_prompts") if key in parent}
    options.update(name="使用新经验重答 · " + parent.get("name", "SFT"), targets=["sft"],
        sources=[], reanswer_sft=True, repair_inputs=selected,
        review_repair={"mode": "auto", "max_rounds": 0, "score_threshold": .8},
        reasoning_trim={"enabled": False}, package_review={"enabled": False}, reasoning_required=True,
        agent_experience=agent_experience, sample_count=len(selected), tasks=len(selected))
    return options


def reanswer_experience(parent, submitted):
    """Only the native SFT author's reasoning experience changes in this action."""
    current = validate_agent_experience(submitted)
    previous = parent["agent_experience"]["scopes"]
    return {scope: deepcopy(current[scope] if scope == "reasoning" else
            {key: settings[key] for key in ("enabled", "text")}) for scope, settings in previous.items()}


def planned_sft_prompt(record, parent_recipe):
    messages = record.get("messages")
    if (not isinstance(messages, list) or len(messages) < 2
            or messages[-1].get("role") != "assistant"
            or messages[-1].get("tool_calls") or messages[-1].get("toolCalls")
            or messages[-1].get("function_call") or record.get("tools")):
        raise ValueError("sft_reanswer_native_dialogue_required")
    prompt = deepcopy(without_injected_public_experience(messages[:-1], parent_recipe))
    if (not prompt or prompt[-1].get("role") != "user"
            or any(message.get("role") not in {"user", "assistant"}
                   or message.get("tool_calls") or message.get("toolCalls") or message.get("function_call") for message in prompt)
            or not isinstance(record.get("qa_contract"), dict)):
        raise ValueError("sft_reanswer_unsupported_history")
    return prompt


def reanswer_task_identity(prompt, contract, evidence):
    return digest({"messages": prompt, "qa_contract": contract,
                   "evidence_sha256": digest(evidence)})


def authenticated_teacher_text(record, evidence):
    """Use the original block join, not the reviewer projection's strip()."""
    blocks = evidence.get("teacher_evidence_blocks")
    text = "\n\n".join(blocks) if isinstance(blocks, list) and blocks else evidence.get("teacher_evidence")
    provided = record.get("provided_source_context") or {}
    if (not isinstance(text, str) or not text
            or provided.get("text_sha256") != digest(text)
            or provided.get("text_chars", len(text)) != len(text)
            or provided.get("scope") not in {"selected_lines", "prepared_source"}):
        raise ValueError("sft_reanswer_source_scope_changed")
    return text


def reanswer_receipt(record, context, prompt, generated, parent_recipe, child_recipe):
    original_task = reanswer_task_identity(prompt, record["qa_contract"], context["evidence"])
    actual = planned_sft_prompt(generated, child_recipe)
    current_task = reanswer_task_identity(actual, generated["qa_contract"], context["evidence"])
    if current_task != original_task:
        raise ValueError("sft_reanswer_task_changed")
    prior_source = record.get("provided_source_context") or {}
    current_source = generated.get("provided_source_context") or {}
    if any(current_source.get(key) != prior_source.get(key) for key in ("scope", "text_sha256")):
        raise ValueError("sft_reanswer_source_scope_changed")
    body = {"version": 1, "parent_run_id": context["parent_run_id"],
            "parent_candidate_id": context["candidate_id"],
            "parent_candidate_sha256": context["candidate_sha256"],
            "parent_context": deepcopy(context), "original_task_sha256": original_task,
            "current_task_sha256": current_task, "question_sha256": digest(prompt),
            "contract_sha256": digest(record["qa_contract"]),
            "evidence_sha256": context["evidence_sha256"],
            "provided_source_sha256": prior_source.get("text_sha256"),
            "parent_experience_sha256": parent_recipe.get("agent_experience", {}).get("sha256"),
            "experience_sha256": child_recipe.get("agent_experience", {}).get("sha256"),
            "generation": "new_native_response", "prior_answer_sent_to_author": False,
            "content_sha256": digest(generated["messages"])}
    return {**body, "sha256": digest(body)}


def valid_reanswer_receipt(record):
    receipt = record.get("sft_reanswer")
    return (isinstance(receipt, dict) and receipt.get("version") == 1
            and receipt.get("sha256") == digest({k: v for k, v in receipt.items() if k != "sha256"})
            and receipt.get("content_sha256") == digest(record.get("messages"))
            and receipt.get("original_task_sha256") == receipt.get("current_task_sha256")
            and receipt.get("provided_source_sha256") == (record.get("provided_source_context") or {}).get("text_sha256")
            and receipt.get("contract_sha256") == digest(record.get("qa_contract")))
