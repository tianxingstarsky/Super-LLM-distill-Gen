"""Stream review candidates and attach provenance only to the requested page."""
from __future__ import annotations

import json

from lib.infrastructure.json_stream import iter_json_records


def iter_review_rows(path, target, validate, identity, *, payload_key="row", id_key="sample_id"):
    seen = set()
    with (path / "artifacts" / f"{target}.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = validate(json.loads(line))
            sample_id = identity(row)
            if sample_id in seen:
                label = "preference_pair" if id_key == "pair_id" else f"{target}_sample"
                raise ValueError(f"duplicate_{label}_id")
            seen.add(sample_id)
            yield {id_key: sample_id, payload_key: row, "evidence": {}}


def attach_review_evidence(items, path, target, identity):
    if not items:
        return items
    wanted = {item["sample_id"]: item for item in items}
    records_path = path / "artifacts" / f"{target}.records.json"
    if not records_path.is_file():
        return items
    fields = ("id", "source_id", "kind", "location", "evidence_level", "judge", "quotes", "citations")
    for record in iter_json_records(records_path):
        if record.get("status") != "eligible":
            continue
        if target == "cpt":
            payload = {"text": record.get("text")}
        else:
            payload = {"messages": record.get("messages")}
            if record.get("tools"):
                payload["tools"] = record["tools"]
        item = wanted.get(identity(payload))
        if item is not None and not item["evidence"]:
            item["evidence"] = {key: record[key] for key in fields if key in record}
    return items
