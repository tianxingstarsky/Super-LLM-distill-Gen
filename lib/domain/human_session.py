"""Bounded human editing drafts and feedback; execution validates completed designs."""
from __future__ import annotations

from copy import deepcopy
import re

from lib.domain.human_augmentation import MAX_HUMAN_SEEDS, MAX_HUMAN_DESIGN_CHARS
from lib.domain.workflow_quality import canonical, text_issue

MAX_SESSION_ROUNDS = 200
MAX_REVISION_DEPTH = 10
MAX_FEEDBACK_CHARS = 6000


def identifier(value, error="invalid_human_session_id"):
    if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError(error)
    return value


def request_identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,100}", value):
        raise ValueError("invalid_human_session_request_id")
    return value


def bounded_text(value, *, limit=MAX_FEEDBACK_CHARS, required=False):
    if (not isinstance(value, str) or len(value) > limit or "\x00" in value
            or (required and not value.strip()) or (value.strip() and text_issue(value))):
        raise ValueError("invalid_human_session_text")
    return value.strip()


def validate_draft(value):
    """Incomplete fields are saved, never submitted as eligible samples."""
    fields = {"enabled", "seeds", "question_requirements", "answer_requirements"}
    if (not isinstance(value, dict) or set(value) - fields
            or type(value.get("enabled", True)) is not bool):
        raise ValueError("invalid_human_session_draft")
    seeds = value.get("seeds", [])
    if not isinstance(seeds, list) or len(seeds) > MAX_HUMAN_SEEDS:
        raise ValueError("invalid_human_session_draft")
    result = {"enabled": value.get("enabled", True), "seeds": []}
    for field in ("question_requirements", "answer_requirements"):
        result[field] = bounded_text(value.get(field, ""))
    for seed in seeds:
        if not isinstance(seed, dict) or set(seed) - {"id", "question", "answer", "question_requirements", "answer_requirements"}:
            raise ValueError("invalid_human_session_draft")
        result["seeds"].append({key: bounded_text(seed.get(key, ""),
            limit=12_000 if key in {"question", "answer"} else MAX_FEEDBACK_CHARS)
            for key in ("question", "answer", "question_requirements", "answer_requirements")})
    if len(canonical(result)) > MAX_HUMAN_DESIGN_CHARS:
        raise ValueError("human_augmentation_size_limit")
    return deepcopy(result)


def validate_feedback(*, instruction="", question=None, answer=None, decision="revise"):
    if decision not in {"revise", "approve", "reject"}:
        raise ValueError("invalid_human_session_feedback")
    result = {"instruction": bounded_text(instruction), "decision": decision}
    for key, value in (("question", question), ("answer", answer)):
        result[key] = None if value is None else bounded_text(value, limit=12_000, required=True)
    if decision == "revise" and not any(result[key] for key in ("instruction", "question", "answer")):
        raise ValueError("human_session_feedback_required")
    return result
