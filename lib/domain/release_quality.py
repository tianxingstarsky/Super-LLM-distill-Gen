"""Pure quality and review-consensus rules for legacy conversation releases.

These checks establish structure and current-content review coverage. They do
not claim semantic correctness or safety beyond the submitted review votes.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import re
from lib.domain.workflow_quality import tool_error_flag


def sample_hash(sample: dict) -> str:
    body = {key: sample[key] for key in ("messages", "images", "tools") if key in sample}
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def update_source_digest(digest, sample_id, fingerprint: str) -> None:
    """Frame each reviewed ID and content hash so row order is unambiguous."""
    item = json.dumps([sample_id, fingerprint], ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")
    digest.update(len(item).to_bytes(8, "big"))
    digest.update(item)


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


def report(samples, decisions=(), *, on_fingerprint=None) -> dict:
    issues: list[dict] = []
    hashes: Counter[str] = Counter()
    ids: Counter[str] = Counter()
    languages: Counter[str] = Counter()
    length_count = length_sum = 0
    length_min = length_max = 0
    source_hashes = {}
    count = 0
    for index, row in enumerate(samples):
        count += 1
        label = row.get("id") or f"row-{index + 1}"
        ids[label] += 1
        fingerprint = sample_hash(row)
        if on_fingerprint is not None:
            on_fingerprint(row.get("id"), fingerprint)
        hashes[fingerprint] += 1
        if row.get("id"):
            source_hashes[row["id"]] = fingerprint
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
        error_flags = [tool_error_flag(message) for message in messages]
        if any(issue for _, issue in error_flags):
            issues.append({"sample": label, "code": "invalid_tool_error_flag"})
        if any(error for error, _ in error_flags):
            issues.append({"sample": label, "code": "unresolved_tool_error"})
        text = "\n".join(message.get("content", "") for message in messages)
        length = len(text)
        if not length_count or length < length_min:
            length_min = length
        if length > length_max:
            length_max = length
        length_count += 1
        length_sum += length
        cjk = bool(re.search(r"[\u4e00-\u9fff]", text))
        latin = bool(re.search(r"[a-zA-Z]", text))
        languages["mixed" if cjk and latin else "zh" if cjk else "en_or_other"] += 1

    duplicates = sum(count - 1 for count in hashes.values() if count > 1)
    duplicate_ids = sum(count - 1 for count in ids.values() if count > 1)
    # A decision for an older payload with the same ID must not approve a new row.
    matched = [decision for decision in decisions
               if source_hashes.get(decision.get("sample_id")) == decision.get("sample_hash")
               and decision.get("sample_hash")]
    summary = decide_gate(matched)
    coverage = len({decision["sample_id"] for decision in matched}) / count if count else 0
    blocks: list[str] = []
    if not count:
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
        "samples": count, "duplicate_content": duplicates, "duplicate_ids": duplicate_ids,
        "issue_count": len(issues), "issues": issues, "language_heuristic": dict(languages),
        "length_chars": {"min": length_min, "max": length_max,
                         "mean": round(length_sum / length_count) if length_count else 0},
        "review_coverage": coverage, "review": summary,
        "ready_for_bulk": not blocks, "block_reasons": blocks,
        "limitations": "Structure and review evidence only; not proof of factual accuracy. Vision requires image-aware review.",
    }
