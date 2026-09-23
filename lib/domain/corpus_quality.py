"""Deterministic, explainable checks for small multilingual CPT batches.

The original text is never rewritten. Normalization is only used for
comparison. These checks do not establish source rights or benchmark purity.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import re
import unicodedata
import zlib

from lib.domain.workflow_quality import text_issue


NEAR_MIN_CHARS = 300
NEAR_SHINGLE = 7
NEAR_JACCARD = 0.90


def comparison_text(text: str) -> str:
    """Preserve numbers and punctuation so versions and equations stay distinct."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def _structure_sensitive(text: str) -> bool:
    """Avoid near-deduplicating code, tables, and equations with tiny edits."""
    return (any(marker in text for marker in ("```", "\t", "{", "}", "==", "=", "->"))
            or bool(re.search(r"(?m)^\s*(?:def|class|function|import|from)\s+\w+", text))
            or "\n|" in text)


def _shingles(text: str) -> set[str]:
    return {text[i:i + NEAR_SHINGLE] for i in range(len(text) - NEAR_SHINGLE + 1)}


def inspect_corpus(text: str) -> dict:
    """Reject only obvious non-text or pathological repetition.

    Short-line and punctuation fractions are diagnostic signals, not English
    web-crawl thresholds applied to Chinese, code, tables or math documents.
    """
    issue = text_issue(text)
    if issue:
        return {"keep": False, "reason": issue, "signals": {}}
    normalized = comparison_text(text)
    visible = [char for char in text if not char.isspace()]
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    substantive_ratio = sum(char.isalnum() for char in visible) / len(visible)
    line_lengths = [len(line) for line in lines]
    duplicate_lines = Counter(line for line in lines if len(line) >= 12)
    repeated_chars = sum((count - 1) * len(line) for line, count in duplicate_lines.items() if count > 1)
    repeated_line_fraction = repeated_chars / max(1, sum(line_lengths))
    shingles = _shingles(normalized)
    shingle_uniqueness = len(shingles) / max(1, len(normalized) - NEAR_SHINGLE + 1)
    signals = {"visible_chars": len(visible),
               "substantive_ratio": round(substantive_ratio, 4),
               "repeated_line_fraction": round(repeated_line_fraction, 4),
               "shingle_uniqueness": round(shingle_uniqueness, 4),
               "short_line_fraction": round(sum(length < 30 for length in line_lengths) / max(1, len(lines)), 4),
               "lines": len(lines)}
    reason = None
    if len(visible) >= 120 and substantive_ratio < 0.10:
        reason = "mostly_nontext_corpus"
    elif len(visible) >= NEAR_MIN_CHARS and repeated_line_fraction >= 0.80:
        reason = "repeated_boilerplate_corpus"
    elif len(visible) >= NEAR_MIN_CHARS and shingle_uniqueness < 0.08:
        reason = "low_information_repetition"
    return {"keep": reason is None, "reason": reason, "signals": signals}


def summarize_corpus_sources(records: list[dict]) -> list[dict]:
    """Explain CPT retention per source using final packaged record states.

    The source identity separates equally named files. Only counts, text
    lengths and reason codes leave this function; quarantined text is never
    copied into the summary.
    """
    groups: dict[tuple[str, str], dict] = {}
    for row in records:
        source_id = str(row.get("source_id") or "unknown")
        source_name = str(row.get("source_name") or "开放需求")
        key = (source_id, source_name)
        if key not in groups:
            groups[key] = {"source_id": source_id, "source_name": source_name,
                           "candidates": 0, "exported": 0, "duplicates": 0,
                           "quarantined": 0, "other": 0,
                           "candidate_chars": 0, "exported_chars": 0,
                           "reasons": {}}
        group = groups[key]
        group["candidates"] += 1
        status = row.get("status")
        field = {"eligible": "exported", "duplicate": "duplicates",
                 "quarantined": "quarantined"}.get(status, "other")
        group[field] += 1
        text = row.get("text")
        chars = (len(text) if isinstance(text, str) else
                 row.get("quarantined_text_chars", 0))
        if type(chars) is not int or chars < 0:
            chars = 0
        group["candidate_chars"] += chars
        if status == "eligible":
            group["exported_chars"] += chars
        reason = row.get("reason")
        if isinstance(reason, str) and reason:
            group["reasons"][reason] = group["reasons"].get(reason, 0) + 1
    return [{**group, "retention_rate": round(group["exported"] / group["candidates"], 4)}
            for group in groups.values()]


class CorpusNearDuplicateIndex:
    """Exact normalized matching, then conservative 7-gram Jaccard matching.

    Stable bottom-hash pairs limit the number of comparisons in a batch. The
    comparison is exact Jaccard for every candidate; pair indexing can miss a
    near duplicate, so quality reports describe this as batch-local filtering.
    """

    def __init__(self):
        self._exact: dict[str, dict] = {}
        self._entries: list[tuple[str, dict]] = []
        self._buckets: dict[tuple[int, int], list[int]] = defaultdict(list)

    @staticmethod
    def _keys(shingles: set[str]) -> tuple[tuple[int, int], ...]:
        hashes = sorted({zlib.crc32(gram.encode("utf-8")) for gram in shingles})[:12]
        return tuple((hashes[index], hashes[index + 1]) for index in range(0, len(hashes) - 1, 2))

    def _find(self, text: str, normalized: str) -> tuple[dict, dict] | None:
        exact = self._exact.get(normalized)
        if exact:
            return ({"reason": "exact_duplicate_corpus", "duplicate_of": exact["id"],
                     "representative_source": exact.get("source_name"), "similarity": 1.0,
                     **({"reference_release": exact["reference_release"]}
                        if "reference_release" in exact else {})}, exact)

        shingles = (_shingles(normalized) if len(normalized) >= NEAR_MIN_CHARS
                    and not _structure_sensitive(text) else set())
        keys = self._keys(shingles) if shingles else ()
        candidates = sorted({index for key in keys for index in self._buckets[key]})
        for index in candidates:
            other_text, other_reference = self._entries[index]
            if min(len(normalized), len(other_text)) < NEAR_MIN_CHARS:
                continue
            if min(len(normalized), len(other_text)) / max(len(normalized), len(other_text)) < 0.80:
                continue
            if re.findall(r"\d+(?:\.\d+)?", normalized) != re.findall(r"\d+(?:\.\d+)?", other_text):
                continue
            other_shingles = _shingles(other_text)
            similarity = len(shingles & other_shingles) / len(shingles | other_shingles)
            if similarity >= NEAR_JACCARD:
                return ({"reason": "near_duplicate_corpus", "duplicate_of": other_reference["id"],
                         "representative_source": other_reference.get("source_name"),
                         "similarity": round(similarity, 4),
                         **({"reference_release": other_reference["reference_release"]}
                            if "reference_release" in other_reference else {})}, other_reference)

        return None

    def match(self, text: str) -> dict | None:
        """Look up a candidate without making it a reference for later queries."""
        found = self._find(text, comparison_text(text))
        return found[0] if found else None

    def add_reference(self, text: str, reference: dict) -> None:
        """Index every verified exemplar, including near variants of earlier ones."""
        normalized = comparison_text(text)
        self._exact.setdefault(normalized, reference)
        shingles = (_shingles(normalized) if len(normalized) >= NEAR_MIN_CHARS
                    and not _structure_sensitive(text) else set())
        keys = self._keys(shingles) if shingles else ()
        if keys:
            index = len(self._entries)
            self._entries.append((normalized, reference))
            for key in keys:
                self._buckets[key].append(index)

    def check_and_add(self, text: str, reference: dict) -> dict | None:
        normalized = comparison_text(text)
        found = self._find(text, normalized)
        if found:
            self._exact[normalized] = found[1]
            return found[0]
        self.add_reference(text, reference)
        return None
