"""Deterministic pre-export checks. Structural checks never claim factual correctness."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re


def sample_hash(sample: dict) -> str:
    body = {k: sample[k] for k in ("messages", "images", "tools") if k in sample}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def read_samples(path: Path) -> list[dict]:
    samples = []
    with Path(path).open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("expected an object")
            except (ValueError, TypeError) as error:
                raise ValueError(f"{path}:{number}: {error}") from error
            samples.append(row)
    return samples


def report(samples: list[dict], decisions=()) -> dict:
    from lib.review import decide_gate
    issues = []
    hashes = Counter()
    ids = Counter()
    languages = Counter()
    lengths = []
    for index, row in enumerate(samples):
        label = row.get("id") or f"row-{index + 1}"
        ids[label] += 1
        hashes[sample_hash(row)] += 1
        if row.get("images"):
            issues.append({"sample": label, "code": "visual_review_required"})
        messages = row.get("messages")
        if not isinstance(messages, list) or not messages:
            issues.append({"sample": label, "code": "messages_missing"})
            continue
        if not all(isinstance(m, dict) and m.get("role") in ("system", "user", "assistant", "tool") and isinstance(m.get("content", ""), str) for m in messages):
            issues.append({"sample": label, "code": "message_schema"})
            continue
        if messages[0].get("role") not in ("system", "user"):
            issues.append({"sample": label, "code": "orphan_context"})
        if messages[-1].get("role") != "assistant" or not (messages[-1].get("content") or messages[-1].get("toolCalls") or messages[-1].get("tool_calls")):
            issues.append({"sample": label, "code": "incomplete_answer"})
        if any(m.get("isError") for m in messages):
            issues.append({"sample": label, "code": "unresolved_tool_error"})
        text = "\n".join(m.get("content", "") for m in messages)
        lengths.append(len(text))
        cjk = bool(re.search(r"[\u4e00-\u9fff]", text))
        latin = bool(re.search(r"[a-zA-Z]", text))
        languages["mixed" if cjk and latin else "zh" if cjk else "en_or_other"] += 1
    duplicates = sum(n - 1 for n in hashes.values() if n > 1)
    duplicate_ids = sum(n - 1 for n in ids.values() if n > 1)
    source_hashes = {r.get("id"): sample_hash(r) for r in samples if r.get("id")}
    # Only reviews bound to this exact content count, not historical responses on reused IDs.
    matched = [d for d in decisions if source_hashes.get(d.get("sample_id")) == d.get("sample_hash") and d.get("sample_hash")]
    summary = decide_gate(matched)
    coverage = len({d["sample_id"] for d in matched}) / len(samples) if samples else 0
    blocks = []
    if not samples:
        blocks.append("empty_dataset")
    if issues:
        blocks.append("structural_errors")
    if duplicates or duplicate_ids:
        blocks.append("duplicate_samples")
    if coverage < 0.9:
        blocks.append("review_coverage_below_90_percent")
    if not summary["release"]:
        blocks.append("review_consensus_not_met")
    return {
        "samples": len(samples), "duplicate_content": duplicates, "duplicate_ids": duplicate_ids,
        "issue_count": len(issues), "issues": issues, "language_heuristic": dict(languages),
        "length_chars": {"min": min(lengths, default=0), "max": max(lengths, default=0), "mean": round(sum(lengths) / len(lengths)) if lengths else 0},
        "review_coverage": coverage, "review": summary,
        "ready_for_bulk": not blocks, "block_reasons": blocks,
        "limitations": "Structure and review evidence only; not proof of factual accuracy. Vision requires image-aware review.",
    }


def export_release(samples, fmt, parent: Path, decisions=(), corpus_path=None, dpo_path=None, tag=None, bulk=False):
    from lib.exporters import export_samples, export_minimind
    from lib.io_utils import atomic_json
    quality = report(samples, decisions)
    if bulk and not quality["ready_for_bulk"]:
        raise ValueError("Quality blocked: " + ", ".join(quality["block_reasons"]))
    if tag and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tag):
        raise ValueError("tag must contain 1-64 letters, digits, underscores or hyphens")
    version = tag or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = Path(parent) / version
    destination.mkdir(parents=True, exist_ok=False)
    atomic_json(destination / "quality.json", quality)
    # Keep an incomplete release visibly incomplete if conversion raises.
    atomic_json(destination / "manifest.json", {"status": "writing", "format": fmt})
    if fmt == "minimind":
        counts = export_minimind(samples, destination / "sft_t2t.jsonl", corpus_path, dpo_path)
    else:
        counts = export_samples(samples, fmt, destination / "sft.jsonl")
    files = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in destination.glob("*.jsonl")}
    atomic_json(destination / "manifest.json", {
        "status": "complete", "created_at": datetime.now(timezone.utc).isoformat(),
        "format": fmt, "bulk": bulk, "counts": counts, "sha256": files,
        "sample_hashes": [sample_hash(s) for s in samples],
    })
    return destination, counts
