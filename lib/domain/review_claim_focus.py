"""Bounded, literal attention cues from current review units, never judgments.

Every marker is an unchanged span of its unit's text, with Python code-point
offsets relative to that text. A match can be a sound condition, quotation or
negation. Missing markers do not establish correctness or reduce review scope.
"""
import re

from lib.domain.review_quote_binding import _protected_positions


_MAX_UNITS = 64
_MAX_MARKERS = 8
_MAX_ID_LENGTH = 128
# Bound multiword whitespace so copied marker text cannot grow without limit.
_CUES = re.compile(
    r"\b(?:only|must(?:n['’]t)?|cannot|can['’]t|never|always|requires?|"
    r"depends?[ \t]{1,8}on|necessarily)\b"
    r"|必须|只能|不可能|不能|只有|一律|永远|依赖|才可|才能",
    re.IGNORECASE,
)


def claim_review_focus_units(target_units: list[dict]) -> dict:
    """Return literal cues in original unit/span order, with explicit overflow.

    Only units containing visible prose cues are returned. At most 64 such
    units and 8 markers per unit are retained; ``truncated`` reports omitted
    cues. Code/math are excluded; ordinary quoted or conditional prose is not.
    The caller must still review all canonical current units in full.
    """
    if not isinstance(target_units, list):
        raise ValueError("invalid_claim_review_focus_units")
    ids = set()
    for unit in target_units:
        if (not isinstance(unit, dict)
                or not isinstance(unit.get("id"), str)
                or not unit["id"].strip()
                or len(unit["id"]) > _MAX_ID_LENGTH
                or unit["id"] in ids
                or not isinstance(unit.get("text"), str)):
            raise ValueError("invalid_claim_review_focus_units")
        ids.add(unit["id"])

    result, truncated = [], False
    for unit in target_units:
        text = unit["text"]
        protected = _protected_positions(text)
        markers = []
        for match in _CUES.finditer(text):
            if any(index in protected for index in range(match.start(), match.end())):
                continue
            if len(result) >= _MAX_UNITS or len(markers) >= _MAX_MARKERS:
                truncated = True
                break
            markers.append({"text": match.group(), "start": match.start(), "end": match.end()})
        if markers:
            result.append({"unit_id": unit["id"], "markers": markers})
        if len(result) >= _MAX_UNITS and truncated:
            break
    return {"units": result, "truncated": truncated}
