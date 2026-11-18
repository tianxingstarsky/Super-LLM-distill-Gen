"""Count original annotation availability as verified OT3 Parquet shards arrive."""

import argparse
from collections import Counter
import json
from pathlib import Path
import time

from filelock import FileLock
import pyarrow.parquet as pq

from . import ot_full_targets as targets
from . import runner


SCHEMA = "openthoughts-original-annotation-audit/v1"


def blank():
    return dict(
        rows=0,
        source_completion=Counter(),
        domain=Counter(),
        source=Counter(),
        multiple_closing_tags=0,
        blank_question=0,
        blank_reasoning=0,
        blank_answer=0,
        char_lengths={},
    )


def length(stats, name, total, maximum, minimum):
    lengths = stats["char_lengths"]
    field = lengths.setdefault(name, {"sum": 0, "max": 0, "min": minimum})
    field["sum"] += total
    field["max"] = max(field["max"], maximum)
    field["min"] = min(field["min"], minimum)


def count_shard(path, entry):
    if path.stat().st_size != entry["size"] or runner.file_sha(path) != entry["sha256"]:
        raise ValueError("shard_digest_mismatch")
    stats = blank()
    with pq.ParquetFile(path) as file:
        if file.metadata.num_rows != entry["rows"]:
            raise ValueError("footer_row_count_mismatch")
        for batch in file.iter_batches(
            batch_size=32, columns=["conversations", "domain", "source"]
        ):
            for row in batch.to_pylist():
                stats["rows"] += 1
                stats["domain"][str(row["domain"])] += 1
                stats["source"][str(row["source"])] += 1
                turns = row["conversations"]
                if not turns or [t["from"] for t in turns] != ["human", "gpt"]:
                    stats["source_completion"]["unsupported_conversation"] += 1
                    continue
                question, response = (t["value"] for t in turns)
                if not isinstance(question, str) or not isinstance(response, str):
                    stats["source_completion"]["missing_text"] += 1
                    continue
                if not response.strip():
                    state, reasoning, answer = "empty_response", "", ""
                else:
                    state, reasoning, answer = targets.split_response(response)
                stats["source_completion"][state] += 1
                stats["multiple_closing_tags"] += response.count("</think>") > 1
                stats["blank_question"] += not question.strip()
                stats["blank_reasoning"] += not reasoning.strip()
                stats["blank_answer"] += not answer.strip()
                for key, value in (
                    ("question", question),
                    ("response", response),
                    ("reasoning", reasoning),
                    ("answer", answer),
                ):
                    size = len(value)
                    length(stats, key, size, size, size)
    if (
        stats["rows"] != entry["rows"]
        or sum(stats["source_completion"].values()) != stats["rows"]
    ):
        raise ValueError("streamed_row_count_mismatch")
    return stats


def publish(root, manifest, parts, errors, running):
    stats = blank()
    for part in parts.values():
        source = part["stats"]
        for key in ("source_completion", "domain", "source"):
            stats[key].update(source[key])
        for key in (
            "rows",
            "multiple_closing_tags",
            "blank_question",
            "blank_reasoning",
            "blank_answer",
        ):
            stats[key] += source[key]
        for key, values in source["char_lengths"].items():
            length(stats, key, values["sum"], values["max"], values["min"])
    done = (
        len(parts) == len(manifest["shards"])
        and stats["rows"] == manifest["total_rows"]
        and not errors
    )
    receipt = dict(
        schema=SCHEMA,
        dataset=manifest["dataset"],
        revision=manifest["revision"],
        scope="Original annotation availability only. No tokenizer, teacher inference, training or grading.",
        boundary_rule="First closing think tag ends a leading thought. Subsequent text stays in the answer. "
        "A nonempty published answer does not certify semantic quality or an intact ending.",
        length_unit="Unicode code points in each observed source field",
        status="complete" if done else ("watching" if running else "partial"),
        expected_rows=manifest["total_rows"],
        expected_shards=len(manifest["shards"]),
        audited_shards=sorted(parts),
        pending_shards=[
            s["index"] for s in manifest["shards"] if s["index"] not in parts
        ],
        errors=errors,
        updated_unix=time.time(),
        **stats,
    )
    runner.write_json(root / "corpus-audit.json", receipt)
    return done


def audit(root, watch, interval):
    manifest = targets.load_manifest(root)
    parts_dir = root / "corpus-audit-parts"
    parts_dir.mkdir(exist_ok=True)
    with FileLock(str(root / "corpus-audit.lock"), timeout=0):
        parts, errors = {}, {}
        for entry in manifest["shards"]:
            index = entry["index"]
            saved = parts_dir / f"{index:03d}.json"
            if saved.exists():
                part = json.loads(saved.read_text("utf-8"))
                if (
                    part["schema"] != SCHEMA
                    or part["shard_sha256"] != entry["sha256"]
                    or part["index"] != index
                    or part["stats"]["rows"] != entry["rows"]
                ):
                    raise ValueError(f"Unbound existing source audit for shard {index}")
                parts[index] = part
        publish(root, manifest, parts, errors, watch)
        while True:
            for entry in manifest["shards"]:
                index = entry["index"]
                path = root / "corpus" / entry["path"]
                if index in parts or not path.is_file():
                    continue
                try:
                    stats = count_shard(path, entry)
                    part = dict(
                        schema=SCHEMA,
                        index=index,
                        shard_sha256=entry["sha256"],
                        stats=stats,
                    )
                    runner.write_json(parts_dir / f"{index:03d}.json", part)
                    parts[index] = part
                    errors.pop(str(index), None)
                    print(
                        f"Verified annotation availability in shard {index} ({stats['rows']} rows)",
                        flush=True,
                    )
                except (OSError, ValueError) as error:
                    errors[str(index)] = dict(type=type(error).__name__, retryable=True)
                publish(root, manifest, parts, errors, watch)
            if publish(root, manifest, parts, errors, watch) or not watch:
                return
            time.sleep(interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--interval", default=15, type=float)
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("--interval must be positive")
    audit(args.root, args.watch, args.interval)
