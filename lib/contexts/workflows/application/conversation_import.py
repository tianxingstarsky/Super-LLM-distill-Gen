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
