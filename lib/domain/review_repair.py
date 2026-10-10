"""One review node owns scoring, correction and the bounded recheck loop."""
from __future__ import annotations

from copy import deepcopy
from math import isfinite

from lib.domain.human_augmentation import validate_revision_context
from lib.domain.workflow_quality import canonical, conversation_issue, text_issue
from lib.domain.workflow_targets import TARGETS, TRAINING_FIELDS


def validate_review_repair(value):
    if value is None:
        return None
    if (not isinstance(value, dict) or set(value) - {"mode", "max_rounds", "score_threshold"}
            or value.get("mode", "auto") not in {"auto", "human"}):
        raise ValueError("invalid_review_repair")
    rounds, threshold = value.get("max_rounds", 2), value.get("score_threshold", .8)
    if (type(rounds) is not int or not 0 <= rounds <= 5 or type(threshold) not in {int, float}
            or not isfinite(threshold) or not 0 <= threshold <= 1):
        raise ValueError("invalid_review_repair")
    return {"mode": value.get("mode", "auto"), "max_rounds": rounds, "score_threshold": float(threshold)}


def validate_repair_inputs(value, config=None):
    if value is None:
        return None
    if not config or not isinstance(value, list) or not 1 <= len(value) <= 200:
        raise ValueError("invalid_review_repair_inputs")
    fields = {"target", "record", "revision_context", "instruction", "question", "answer", "messages", "score", "approved", "corrected_record"}
    result, identities = [], set()
    for item in value:
        if (not isinstance(item, dict) or not {"target", "record", "revision_context"} <= set(item)
                or set(item) - fields or item["target"] not in TARGETS or not isinstance(item["record"], dict)):
            raise ValueError("invalid_review_repair_inputs")
        row = deepcopy(item)
        row["revision_context"] = validate_revision_context(row["revision_context"], review=True)
        if row["target"] != row["revision_context"]["target"]:
            raise ValueError("invalid_review_repair_inputs")
        identity = (row["revision_context"]["parent_run_id"], row["target"], row["revision_context"]["candidate_id"])
        if identity in identities:
            raise ValueError("duplicate_review_repair_input")
        identities.add(identity)
        for field, limit in (("instruction", 6000), ("question", 24000), ("answer", 24000)):
            if field in row and row[field] is not None:
                if not isinstance(row[field], str) or len(row[field]) > limit or "\x00" in row[field] or (row[field].strip() and text_issue(row[field])):
                    raise ValueError("invalid_review_repair_inputs")
        if "messages" in row and (not isinstance(row["messages"], list) or len(row["messages"]) > 17
                or conversation_issue(row["messages"])):
            raise ValueError("invalid_review_repair_inputs")
        if "corrected_record" in row:
            patch = row["corrected_record"]
            if (not isinstance(patch, dict) or set(patch) - set(TRAINING_FIELDS[row["target"]])
                    or len(canonical(patch)) > 100_000):
                raise ValueError("invalid_review_repair_inputs")
        if "score" in row and (type(row["score"]) not in {int, float} or not isfinite(row["score"]) or not 0 <= row["score"] <= 1):
            raise ValueError("invalid_review_repair_inputs")
        if "approved" in row and type(row["approved"]) is not bool:
            raise ValueError("invalid_review_repair_inputs")
        if config["mode"] == "human" and not {"score", "approved"} <= set(row):
            raise ValueError("manual_review_score_required")
        result.append(row)
    if len(canonical(result)) > 20_000_000:
        raise ValueError("review_repair_inputs_too_large")
    return result
