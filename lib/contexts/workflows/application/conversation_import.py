"""Preview validated external conversions, then explicitly add selected sources."""
from __future__ import annotations

import json
from typing import Protocol

from ..domain.conversation_import import (
    MAX_IMPORT_BYTES, import_digest, import_eligibility, inspect_import, iter_import_records, validate_import_name,
)


class ConversationImportPort(Protocol):
    def convert(self, name: str, content: bytes) -> tuple[bytes, dict]: ...
    def stage(self, name: str, source_sha256: str, normalized: bytes, report: dict) -> str: ...
    def load(self, import_id: str) -> tuple[dict, bytes, dict]: ...
    def store_source(self, name: str, content: bytes) -> list[dict]: ...


class ImportConversations:
    def __init__(self, port: ConversationImportPort):
        self._port = port

    def preview(self, name: str, content: bytes, *, preview_limit: int = 3) -> dict:
        validate_import_name(name)
        if not isinstance(content, bytes) or not content or len(content) > MAX_IMPORT_BYTES:
            raise ValueError("conversation_import_invalid_input_size")
        source_sha256 = import_digest(content)
        normalized, report = self._port.convert(name, content)
        counts, conversations = inspect_import(normalized, report, source_sha256, preview_limit)
        import_id = self._port.stage(name, source_sha256, normalized, report)
        return {"import_id": import_id, "source_name": name, "source_sha256": source_sha256,
                "counts": counts, "conversations": conversations, "report": report,
                "eligibility": import_eligibility(normalized)}

    def evolution_sources(self, import_id: str, selected_ids: list[str] | None = None, *,
                          max_records: int = 8, max_chars: int = 60000, after_id: str | None = None) -> dict:
        """Read complete staged trajectories for experience proposals, never training approval.

        Training-ineligible failures remain useful observations here. Selection
        is bounded by whole-record JSON characters; oversized trajectories are
        reported rather than losing their intermediate failures or outcomes.
        """
        if not isinstance(import_id, str) or not import_id.strip():
            raise ValueError("conversation_import_invalid_id")
        if after_id is not None and (not isinstance(after_id, str) or not after_id):
            raise ValueError("conversation_import_invalid_evolution_cursor")
        if (type(max_records) is not int or not 1 <= max_records <= 100
                or type(max_chars) is not int or not 1 <= max_chars <= 1000000):
            raise ValueError("conversation_import_invalid_evolution_limits")
        if selected_ids is not None and (
                not isinstance(selected_ids, list) or not selected_ids
                or any(not isinstance(value, str) or not value for value in selected_ids)
                or len(set(selected_ids)) != len(selected_ids)):
            raise ValueError("conversation_import_invalid_selection")
        metadata, normalized, report = self._port.load(import_id)
        source_counts, _ = inspect_import(normalized, report, metadata["source_sha256"], preview_limit=1)
        selected = set(selected_ids) if selected_ids is not None else None
        matched, all_matched, records, positions, skipped = set(), set(), [], [], []
        selected_chars, skipped_count = 2, 0  # JSON array framing is part of the model input.
        cursor_found, cursor_index, batch_full = after_id is None, 0, False
        for position, record in enumerate(iter_import_records(normalized), 1):
            identifier = record["id"]
            if selected is not None and identifier in selected:
                all_matched.add(identifier)
            if not cursor_found:
                if identifier == after_id:
                    cursor_found, cursor_index = True, position
                continue
            if selected is not None and identifier not in selected:
                continue
            matched.add(identifier)
            chars = len(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            additional_chars = chars + int(bool(records))
            reason = ("record_char_limit" if chars + 2 > max_chars else
                      "record_count_limit" if len(records) >= max_records else
                      "selection_char_limit" if batch_full or selected_chars + additional_chars > max_chars else None)
            if reason:
                batch_full = batch_full or reason == "selection_char_limit"
                skipped_count += 1
                if len(skipped) < 100:
                    skipped.append({"id": identifier, "reason": reason, "chars": chars})
                continue
            records.append(record)
            positions.append(position)
            selected_chars += additional_chars
        if not cursor_found:
            raise ValueError("conversation_import_unknown_evolution_cursor")
        if selected is not None and all_matched != selected:
            raise ValueError("conversation_import_unknown_selection")
        if not records:
            raise ValueError("conversation_import_evolution_no_record_fits_limits")
        return {"purpose": "experience_evolution_not_training", "import_id": import_id,
                "source_name": metadata["source_name"], "source_sha256": metadata["source_sha256"],
                "records": records, "selected_ids": [record["id"] for record in records],
                "after_id": after_id,
                "next_after_id": records[-1]["id"] if positions[-1] < source_counts["records"] else None,
                "position": {"after_index": cursor_index, "first_selected_index": positions[0],
                             "last_selected_index": positions[-1]},
                "requested_ids": list(selected_ids) if selected_ids is not None else None,
                "counts": {"total_records": source_counts["records"], "matched_records": len(matched),
                           "selected_records": len(records), "selected_chars": selected_chars,
                           "skipped_records": skipped_count},
                "limits": {"max_records": max_records, "max_chars": max_chars},
                "skipped": skipped, "skipped_truncated": skipped_count > len(skipped)}

    def confirm(self, import_id: str, selected_ids: list[str] | None = None, *, target: str = "sft") -> list[dict]:
        metadata, normalized, report = self._port.load(import_id)
        inspect_import(normalized, report, metadata["source_sha256"])
        if target not in {"sft", "agent"}:
            raise ValueError("conversation_import_invalid_target")
        eligibility = import_eligibility(normalized)[target]
        if not eligibility["eligible_records"]:
            raise ValueError("conversation_import_training_ineligible")
        if selected_ids is None and eligibility["blocked_records"]:
            raise ValueError("conversation_import_selection_required")
        if selected_ids is not None:
            if (not isinstance(selected_ids, list) or not selected_ids
                    or any(not isinstance(value, str) or not value for value in selected_ids)
                    or len(set(selected_ids)) != len(selected_ids)):
                raise ValueError("conversation_import_invalid_selection")
            selected = set(selected_ids)
            matched = set()
            lines = []
            for record in iter_import_records(normalized):
                if record["id"] in selected:
                    from ..domain.conversation_import import training_issues
                    if training_issues(record)[target]:
                        raise ValueError("conversation_import_training_ineligible")
                    matched.add(record["id"])
                    lines.append(json.dumps(record, ensure_ascii=False) + "\n")
            if matched != selected:
                raise ValueError("conversation_import_unknown_selection")
            if len(matched) != report["records"]:
                normalized = "".join(lines).encode("utf-8")
        name = "conversations-" + metadata["source_sha256"][:16] + ".jsonl"
        return self._port.store_source(name, normalized)
