"""Opt-in native answer generation and its lossless training-format projection."""
from __future__ import annotations

from copy import deepcopy


def validate_new_native_generation_route(generation: dict, targets):
    """Do not discard an explicitly configured CoT stage when creating a run."""
    sft = generation.get("sft", {})
    if (sft.get("enabled") is True and sft.get("answer_mode") == "native_joint"
            and "cot" in targets and "cot" in generation):
        raise ValueError("native_generation_conflicting_cot_stage")


def normalize_new_reading_generation(generation: dict, director: dict | None):
    """New reading tasks use the ordinary director, never a hidden second planner.

    This is called only before freezing a new recipe. Saved reading-session
    recipes retain their dedicated scene/user/answer checkpoints.
    """
    config = generation.get("multiturn", {})
    if not config.get("reading_session"):
        return generation, director
    from lib.domain.workflow_qa_director import DEFAULT_TYPE_WEIGHTS, QA_TYPES
    normalized = deepcopy(generation)
    normalized["multiturn"].pop("reading_session")
    normalized["multiturn"].update(enabled=True, answer_mode="native_joint")
    if director is not None and not isinstance(director, dict):
        raise ValueError("invalid_qa_director")
    reading_director = deepcopy(director or {})
    reading_director.update(enabled=True, learning_goal="document_reading", question_separation=True)
    reading_director.pop("learning_goal_mix", None)
    for key, value in {"planning_mode": "adaptive", "context_mode": "persistent",
                       "tools_mode": "auto", "user_languages": ["zh", "en"]}.items():
        reading_director.setdefault(key, value)
    weights = reading_director.setdefault("type_weights", deepcopy(DEFAULT_TYPE_WEIGHTS))
    if (isinstance(weights, dict) and set(weights) == set(QA_TYPES)
            and all(type(weight) is int and 0 <= weight <= 100 for weight in weights.values())):
        weights["closed_book"] = 0
        if not any(weights.values()):
            weights["grounded"] = 100
    return normalized, reading_director


def native_generation_enabled(recipe: dict, stage: str) -> bool:
    """Frozen legacy recipes never adopt the integrated route implicitly."""
    config = recipe.get("node_generation", {}).get(stage, {})
    return (recipe.get("native_generation_version") == 1
            and stage in {"sft", "multiturn"}
            and config.get("enabled") is True
            and config.get("answer_mode") == "native_joint")


def native_sft_to_cot_record(sample: dict) -> dict:
    """Expose the same native response as CoT, without another author or judge."""
    row = deepcopy(sample)
    messages = row.get("messages", [])
    final = messages[-1] if messages and isinstance(messages[-1], dict) else {}
    reasoning, answer = final.get("reasoning_content", ""), final.get("content", "")
    row.update(question=deepcopy(messages[:-1]), reasoning=[reasoning], answer=answer,
               reasoning_route="sft_native_projection")
    if (final.get("role") != "assistant" or not isinstance(reasoning, str)
            or not reasoning.strip() or not isinstance(answer, str) or not answer.strip()):
        row.update(status="quarantined", reason="cot_missing_reasoning_or_answer")
    return row
