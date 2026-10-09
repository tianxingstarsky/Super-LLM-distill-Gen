"""Conservative, node-scoped cleaning of source material for CPT."""
from __future__ import annotations


def validate_cpt_processing(value: dict | None = None) -> dict:
    """Absence means the historical deterministic path, never paid calls."""
    if value is None:
        return {"mode": "native"}
    if not isinstance(value, dict) or set(value) - {"mode", "review_mode"}:
        raise ValueError("invalid_cpt_processing")
    mode = value.get("mode", "model")
    review = value.get("review_mode", "text")
    if (not isinstance(mode, str) or mode not in {"native", "model"}
            or not isinstance(review, str) or review not in {"text", "vision"}):
        raise ValueError("invalid_cpt_processing")
    if mode == "native":
        if "review_mode" in value:
            raise ValueError("invalid_cpt_processing")
        return {"mode": "native"}
    return {"mode": "model", "review_mode": review}


def cleaned_corpus(value: dict) -> dict:
    """A malformed successful call must be retried, not silently discarded."""
    if (not isinstance(value, dict)
            or set(value) != {"text", "uncertain", "removed_noise", "reason"}
            or not isinstance(value.get("text"), str)
            or len(value["text"]) > 80_000
            or type(value.get("uncertain")) is not bool
            or not isinstance(value.get("removed_noise"), list)
            or len(value["removed_noise"]) > 20
            or any(not isinstance(item, str) or not item.strip() or len(item) > 300
                   for item in value["removed_noise"])
            or not isinstance(value.get("reason"), str)
            or not value["reason"].strip() or len(value["reason"]) > 2_000):
        raise ValueError("invalid_cpt_clean_schema")
    return value


def corpus_preservation(value: dict) -> dict:
    """The independent reviewer must check each preservation dimension."""
    from lib.domain.workflow_quality import verdict
    verdict(value)
    fields = {"source_faithful", "numbers_and_units_preserved", "formulas_and_code_preserved",
              "coverage_sufficient"}
    preservation = value.get("preservation")
    if (not isinstance(preservation, dict) or set(preservation) != fields
            or any(type(item) is not bool for item in preservation.values())):
        raise ValueError("invalid_cpt_review_schema")
    return value
