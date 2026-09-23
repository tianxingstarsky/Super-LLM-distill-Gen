"""Pure rules for reviewing preference pairs before a human-reviewed release."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json


PROMPT_ROLES = frozenset({"system", "developer", "user", "assistant", "tool"})


def pair_identity(pair: dict) -> str:
    """Stable identity for one exact preference candidate, including context and tools."""
    encoded = json.dumps(pair, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_pair(pair: dict) -> dict:
    if not isinstance(pair, dict):
        raise ValueError("preference_pair_must_be_object")
    prompt = pair.get("prompt")
    if not isinstance(prompt, list) or not prompt:
        raise ValueError("preference_prompt_required")
    for message in prompt:
        if not isinstance(message, dict) or message.get("role") not in PROMPT_ROLES:
            raise ValueError("invalid_preference_prompt")
    for field in ("chosen", "rejected"):
        messages = pair.get(field)
        if not isinstance(messages, list) or len(messages) != 1:
            raise ValueError(f"preference_{field}_required")
        if any(not isinstance(message, dict) or message.get("role") != "assistant"
               or not isinstance(message.get("content"), str) or not message["content"].strip()
               for message in messages):
            raise ValueError(f"invalid_preference_{field}")
    chosen = "\n".join(message["content"].strip() for message in pair["chosen"])
    rejected = "\n".join(message["content"].strip() for message in pair["rejected"])
    if chosen == rejected:
        raise ValueError("preference_answers_must_differ")
    return deepcopy(pair)


def review_record(pair: dict, *, decision: str, reviewer: str, reviewed_at: str,
                  reason: str = "", chosen: str | None = None,
                  rejected: str | None = None, target: str = "dpo") -> dict:
    """Build an auditable decision while preserving prompt and message metadata."""
    source = validate_pair(pair)
    if target not in {"dpo", "orpo"}:
        raise ValueError("invalid_preference_review_target")
    if decision not in {"approved", "rejected", "skipped"}:
        raise ValueError("invalid_preference_decision")
    if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 128:
        raise ValueError("invalid_reviewer")
    if not isinstance(reason, str) or len(reason) > 2000:
        raise ValueError("invalid_review_reason")
    candidate = deepcopy(source)
    if decision == "approved":
        chosen_text = candidate["chosen"][-1]["content"] if chosen is None else chosen
        rejected_text = candidate["rejected"][-1]["content"] if rejected is None else rejected
        if not isinstance(chosen_text, str) or not isinstance(rejected_text, str):
            raise ValueError("preference_revisions_must_be_text")
        if len(chosen_text) > 40000 or len(rejected_text) > 40000:
            raise ValueError("preference_revision_too_long")
        if not chosen_text.strip() or not rejected_text.strip() or chosen_text.strip() == rejected_text.strip():
            raise ValueError("invalid_preference_revision")
        candidate["chosen"][-1]["content"] = chosen_text
        candidate["rejected"][-1]["content"] = rejected_text
    return {
        "target": target,
        "pair_id": pair_identity(source),
        "source_hash": pair_identity(source),
        "decision": decision,
        "reviewer": reviewer.strip(),
        "reviewed_at": reviewed_at,
        "reason": reason.strip(),
        "candidate": candidate,
    }
