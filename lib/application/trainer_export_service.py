"""Prepare optional TRL artifacts with an all-or-nothing compatibility gate.

The service is pure: callers own file writes and can inspect every rejected
record before deciding whether to offer a downloadable trainer artifact.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from lib.domain.trainer_formats import to_trl_preference, to_trl_sft


_CONVERTERS = {
    "sft": to_trl_sft,
    "multiturn": to_trl_sft,
    "agent": to_trl_sft,
    "dpo": to_trl_preference,
    "orpo": to_trl_preference,
}


def prepare_trl_export(target: str, records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Return atomic TRL rows plus row-level compatibility diagnostics.

    ``rows`` is intentionally empty if even one source row fails conversion.
    ``summary.compatible`` still counts records that would have converted, so
    the UI can explain the gap without offering a partial training artifact.
    """
    if target not in _CONVERTERS:
        raise ValueError("trl_unsupported_target")
    if isinstance(records, (str, bytes, Mapping)) or not isinstance(records, Iterable):
        raise ValueError("trl_records_must_be_iterable")

    converter = _CONVERTERS[target]
    rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    total = 0
    for index, record in enumerate(records):
        total += 1
        record_id = record.get("id") if isinstance(record, Mapping) else None
        identity = str(record_id)[:128] if record_id is not None else None
        if not isinstance(record, Mapping):
            reason = "trl_record_must_be_object"
        elif record.get("status") not in (None, "eligible"):
            reason = "trl_ineligible_record"
        else:
            try:
                rows.append(converter(record))
                continue
            except ValueError as exc:
                reason = str(exc)
        failures.append({"index": index, "id": identity, "reason": reason})

    if not total:
        failures.append({"index": None, "id": None, "reason": "trl_no_records"})
    reasons: dict[str, int] = {}
    for failure in failures:
        reason = failure["reason"]
        reasons[reason] = reasons.get(reason, 0) + 1
    ready = bool(total) and not failures
    return {
        "target": target,
        "format": "trl_sft" if target in {"sft", "multiturn", "agent"} else "trl_preference",
        "ready": ready,
        "rows": rows if ready else [],
        "summary": {"total": total, "compatible": len(rows), "incompatible": total - len(rows),
                    "reasons": dict(sorted(reasons.items()))},
        "failures": failures,
    }
