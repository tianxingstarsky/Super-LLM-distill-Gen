"""Separate a learner's actual context from a teacher's private evidence."""
from __future__ import annotations

from copy import deepcopy


def prompt_at_least(recipe, prompt_id, version):
    """Pinned prompt versions opt new runs in without migrating old requests."""
    pin = (recipe.get("prompts") or {}).get(prompt_id) or {}
    try:
        current = tuple(int(part) for part in pin["version"].split("."))
    except (KeyError, ValueError, TypeError, AttributeError):
        return False
    return len(current) == 3 and current >= version


def learner_contract(contract):
    """Only the requested task category is available to a blinded reviewer.

    Even planned question/context fields could rescue information missing from
    the actual exported messages. Guidance, quotes and design notes are never
    evidence that a learner received those facts.
    """
    if not isinstance(contract, dict):
        return None
    return {key: contract[key] for key in ("qa_type", "answer_policy", "learning_goal", "user_language") if key in contract}


def learner_messages(target, payload):
    """Project the exact training content; preference negatives are excluded."""
    if target in {"sft", "multiturn", "agent"}:
        return deepcopy(payload.get("messages", []))
    if target in {"dpo", "orpo", "rlaif"}:
        return deepcopy([*payload.get("prompt", []), *payload.get("chosen", [])])
    if target == "cot":
        question = payload.get("question")
        prefix = question if isinstance(question, list) else [{"role": "user", "content": question}]
        reasoning = payload.get("reasoning", "")
        if isinstance(reasoning, list):
            reasoning = "\n".join(reasoning)
        return deepcopy([*prefix, {"role": "assistant", "content": payload.get("answer", ""),
                                   "reasoning_content": reasoning}])
    return []
