"""Validate persisted review events against their immutable source candidates."""
from __future__ import annotations

from lib.domain.corpus_review import corpus_identity, validate_corpus_row
from lib.domain.preference_review import pair_identity, validate_pair
from lib.domain.sft_review import sft_identity, validate_sft_revision


def validate_review_event(target: str, source: dict, record: dict) -> dict:
    preference = target in {"dpo", "orpo"}
    family = "preference" if preference else target
    if not isinstance(record, dict):
        raise ValueError(f"invalid_{family}_review_record")
    if record.get("target", "dpo" if preference else target) != target:
        raise ValueError(f"{family}_review_target_mismatch")
    identity = (pair_identity if preference else sft_identity if target == "sft" else corpus_identity)(source)
    id_key = "pair_id" if preference else "sample_id"
    if record.get(id_key) != identity or record.get("source_hash") != identity:
        noun = "preference_pair" if preference else f"{target}_sample"
        raise ValueError(f"stale_{noun}_refresh_required")
    decision = record.get("decision")
    if decision not in {"approved", "rejected", "skipped"}:
        raise ValueError(f"invalid_{family}_decision")
    reviewer = record.get("reviewer")
    if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 128:
        raise ValueError("invalid_reviewer")
    when, reason = record.get("reviewed_at"), record.get("reason", "")
    if not isinstance(when, str) or not when.strip() or not isinstance(reason, str) or len(reason) > 2000:
        raise ValueError(f"invalid_{family}_review_record")
    if target == "sft":
        candidate = validate_sft_revision(source, record.get("candidate"))
    elif target == "cpt":
        candidate = validate_corpus_row(record.get("candidate"))
    elif preference:
        candidate = validate_pair(record.get("candidate"))
        if set(candidate) != set(source) or any(candidate[key] != source[key]
                for key in source if key not in {"chosen", "rejected"}):
            raise ValueError("preference_context_is_immutable")
        for field in ("chosen", "rejected"):
            original, revised = source[field][0], candidate[field][0]
            if set(original) != set(revised) or any(original[key] != revised[key]
                    for key in original if key != "content"):
                raise ValueError("preference_message_metadata_is_immutable")
    else:
        raise ValueError("invalid_review_target")
    if decision != "approved" and candidate != source:
        noun = "preference_pairs" if preference else f"{target}_samples"
        raise ValueError(f"only_approved_{noun}_may_be_revised")
    return record
