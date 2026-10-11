"""Bounded human editing drafts and feedback; execution validates completed designs."""
from __future__ import annotations

from copy import deepcopy
import re

from lib.domain.human_augmentation import MAX_HUMAN_SEEDS, MAX_HUMAN_DESIGN_CHARS
from lib.domain.workflow_quality import canonical, text_issue

MAX_SESSION_ROUNDS = 200
MAX_REVISION_DEPTH = 10
MAX_FEEDBACK_CHARS = 6000
MAX_ISSUE_BATCH = 200
MAX_ISSUE_BATCH_CHARS = 20_000_000


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


def validate_issue_selection(selections, *, instruction=""):
    """A saved issue may have no note yet; launching requires an actual instruction."""
    from lib.domain.workflow_targets import TARGETS
    instruction = bounded_text(instruction)
    if not isinstance(selections, list) or not 1 <= len(selections) <= MAX_ISSUE_BATCH:
        raise ValueError("invalid_human_session_issue_selection")
    rows, identities = [], set()
    for item in selections:
        if (not isinstance(item, dict) or not {"round_id", "target", "candidate_id"} <= set(item)
                or set(item) - {"round_id", "target", "candidate_id", "instruction", "question", "answer", "corrected_record"}
                or item["target"] not in TARGETS):
            raise ValueError("invalid_human_session_issue_selection")
        round_id = identifier(item["round_id"], "invalid_human_session_round_id")
        candidate_id = bounded_text(item["candidate_id"], limit=500, required=True)
        own_instruction = bounded_text(item.get("instruction", ""))
        combined = bounded_text("\n".join(dict.fromkeys(value for value in (own_instruction, instruction) if value)))
        identity = (round_id, item["target"], candidate_id)
        if identity in identities:
            raise ValueError("invalid_human_session_issue_selection")
        identities.add(identity)
        row = {"round_id": round_id, "target": item["target"], "candidate_id": candidate_id, "instruction": combined}
        for field in ("question", "answer"):
            if item.get(field) is not None:
                if item["target"] not in {"sft", "multiturn", "cot"}:
                    raise ValueError("manual_review_patch_target_mismatch")
                row[field] = bounded_text(item[field], limit=12_000, required=True)
        if item.get("corrected_record") is not None:
            from lib.domain.workflow_targets import TRAINING_FIELDS
            patch = item["corrected_record"]
            try:
                valid = (isinstance(patch, dict) and patch and not set(patch) - set(TRAINING_FIELDS[item["target"]])
                         and len(canonical(patch)) <= 100_000 and not text_issue(canonical(patch)))
            except (TypeError, ValueError):
                valid = False
            if not valid or row.get("question") is not None or row.get("answer") is not None:
                raise ValueError("invalid_manual_review_record")
            row["corrected_record"] = deepcopy(patch)
        rows.append(row)
    return rows
