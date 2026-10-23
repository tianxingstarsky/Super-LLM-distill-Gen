"""Pure rules for human review of CPT corpus candidates."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json


def corpus_identity(row: dict) -> str:
    return hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def validate_corpus_row(row: dict) -> dict:
    if not isinstance(row, dict) or set(row) != {"text"}:
        raise ValueError("invalid_cpt_record")
    if not isinstance(row["text"], str) or not row["text"].strip():
        raise ValueError("empty_cpt_record")
    if len(row["text"]) > 500000:
        raise ValueError("cpt_record_too_long")
    return deepcopy(row)


def corpus_review_record(row: dict, *, decision: str, reviewer: str,
                         reason: str = "", text: str | None = None) -> dict:
    source = validate_corpus_row(row)
    if decision not in {"approved", "rejected", "skipped"}:
        raise ValueError("invalid_corpus_decision")
    if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 128:
        raise ValueError("invalid_reviewer")
    if not isinstance(reason, str) or len(reason) > 2000:
        raise ValueError("invalid_review_reason")
    candidate = deepcopy(source)
    if decision == "approved" and text is not None:
        if not isinstance(text, str) or not text.strip() or len(text) > 500000:
            raise ValueError("invalid_cpt_revision")
        candidate["text"] = text
    return {"sample_id": corpus_identity(source), "source_hash": corpus_identity(source),
            "decision": decision, "reviewer": reviewer.strip(),
            "reviewed_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason.strip(), "candidate": candidate}
