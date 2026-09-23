"""Pure quality and review-consensus rules for legacy conversation releases.

These checks establish structure and current-content review coverage. They do
not claim semantic correctness or safety beyond the submitted review votes.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import re


def sample_hash(sample: dict) -> str:
    body = {key: sample[key] for key in ("messages", "images", "tools") if key in sample}
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def decide_gate(decisions: list[dict], threshold: float = 0.9, minimum: int = 10) -> dict:
    """Count distinct reviewed samples, with conflicting votes never kept."""
    by_sample: dict[str, set[str]] = {}
    for decision in decisions:
        by_sample.setdefault(decision["sample_id"], set()).add(decision["decision"])
    total = len(by_sample)
    keeps = sum(values == {"keep"} for values in by_sample.values())
    rate = keeps / total if total else 0.0
    return {
        "reviewed": total,
        "responses": len(decisions),
        "conflicts": sum(len(values) > 1 for values in by_sample.values()),
        "keep": keeps,
        "reject": total - keeps,
        "pass_rate": round(rate, 3),
        "release": total >= minimum and rate >= threshold,
    }


def report(samples: list[dict], decisions=()) -> dict:
    issues: list[dict] = []
    hashes: Counter[str] = Counter()
    ids: Counter[str] = Counter()
    languages: Counter[str] = Counter()
    lengths: list[int] = []
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
        if not all(isinstance(message, dict)
                   and message.get("role") in ("system", "user", "assistant", "tool")
                   and isinstance(message.get("content", ""), str) for message in messages):
            issues.append({"sample": label, "code": "message_schema"})
            continue
        if messages[0].get("role") not in ("system", "user"):
            issues.append({"sample": label, "code": "orphan_context"})
        if (messages[-1].get("role") != "assistant"
                or not (messages[-1].get("content") or messages[-1].get("toolCalls")
                        or messages[-1].get("tool_calls"))):
            issues.append({"sample": label, "code": "incomplete_answer"})
        if any(message.get("isError") for message in messages):
            issues.append({"sample": label, "code": "unresolved_tool_error"})
        text = "\n".join(message.get("content", "") for message in messages)
        lengths.append(len(text))
        cjk = bool(re.search(r"[\u4e00-\u9fff]", text))
        latin = bool(re.search(r"[a-zA-Z]", text))
        languages["mixed" if cjk and latin else "zh" if cjk else "en_or_other"] += 1

    duplicates = sum(count - 1 for count in hashes.values() if count > 1)
    duplicate_ids = sum(count - 1 for count in ids.values() if count > 1)
    source_hashes = {row.get("id"): sample_hash(row) for row in samples if row.get("id")}
    # A decision for an older payload with the same ID must not approve a new row.
    matched = [decision for decision in decisions
               if source_hashes.get(decision.get("sample_id")) == decision.get("sample_hash")
               and decision.get("sample_hash")]
    summary = decide_gate(matched)
    coverage = len({decision["sample_id"] for decision in matched}) / len(samples) if samples else 0
    blocks: list[str] = []
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
        "length_chars": {"min": min(lengths, default=0), "max": max(lengths, default=0),
                         "mean": round(sum(lengths) / len(lengths)) if lengths else 0},
        "review_coverage": coverage, "review": summary,
        "ready_for_bulk": not blocks, "block_reasons": blocks,
        "limitations": "Structure and review evidence only; not proof of factual accuracy. Vision requires image-aware review.",
    }
