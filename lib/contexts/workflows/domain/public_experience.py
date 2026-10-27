"""Literal user-owned working principles carried in actual learner context."""
from copy import deepcopy
from lib.domain.agent_experience import INITIAL_STYLE_V1

def initial_experience_text(scope="reasoning"):
    if scope not in {"director", "question", "answer", "reasoning", "review", "cpt"}:
        raise ValueError("invalid_agent_experience_scope")
    return INITIAL_STYLE_V1


def public_experience_text(recipe, scope="reasoning"):
    if recipe.get("native_experience_version") != 1:
        return ""
    selected = recipe.get("agent_experience", {}).get("scopes", {}).get(scope, {})
    if not selected.get("enabled"):
        return ""
    return selected["text"]


def with_public_experience(messages, text):
    copied = deepcopy(messages)
    if text:
        copied.insert(0, {"role": "system", "content": "<working_principles>\n" + text + "\n</working_principles>"})
    return copied
