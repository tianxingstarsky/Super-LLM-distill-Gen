"""Literal user-owned working principles carried in actual learner context."""
from copy import deepcopy
from lib.domain.agent_experience import INITIAL_STYLE_V2
from .dialogue import dialogue_digest

def initial_experience_text(scope="reasoning"):
    if scope not in {"director", "question", "answer", "reasoning", "review", "cpt"}:
        raise ValueError("invalid_agent_experience_scope")
    return INITIAL_STYLE_V2


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


def _has_frozen_public_experience(messages, recipe):
    """Recognize only the exact leading message owned by this frozen run."""
    version = recipe.get("native_experience_version")
    if type(version) is not int or version != 1 or not isinstance(messages, list):
        return False
    text = public_experience_text(recipe)
    return bool(text and messages and messages[0] == with_public_experience([], text)[0])


def without_injected_public_experience(messages, recipe):
    """Project actor turns; unknown systems and modified wrappers stay rejected."""
    return messages[1:] if _has_frozen_public_experience(messages, recipe) else messages


def public_experience_provenance(messages, recipe):
    """Certify source and export intent, never content quality or safety."""
    scope = recipe.get("dialogue_context_scope_version")
    if type(scope) is not int or scope != 1 or not _has_frozen_public_experience(messages, recipe):
        return None
    return {"version": 1, "message_index": 0, "message_sha256": dialogue_digest(messages[0]),
            "origin": "program_frozen_public_experience",
            "visibility": "learner_system_message_exported_verbatim",
            "content_quality": "not_assessed", "authority": "message_provenance_only_not_quality_approval"}
