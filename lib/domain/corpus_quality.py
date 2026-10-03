"""Deterministic, explainable checks for small multilingual CPT batches.

The original text is never rewritten. Normalization is only used for
comparison. These checks do not establish source rights or benchmark purity.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path
import re
import sqlite3
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

    def __init__(self, sqlite_path: Path | None = None):
        self._exact: dict[str, dict] = {}
        self._entries: list[tuple[str, dict]] = []
        self._buckets: dict[tuple[int, int], list[int]] = defaultdict(list)
        self._database = None
        self._disk_backed = sqlite_path is not None
        if sqlite_path is not None:
            self._database = sqlite3.connect(sqlite_path)
            try:
                # These are disposable package indexes. Keep SQLite's page
                # cache and query scratch space bounded for large release sets.
                self._database.execute("PRAGMA cache_size = -2048")
                self._database.execute("PRAGMA temp_store = FILE")
                self._database.executescript("""
                    DROP TABLE IF EXISTS corpus_exact;
                    DROP TABLE IF EXISTS corpus_buckets;
                    DROP TABLE IF EXISTS corpus_entries;
                    CREATE TABLE corpus_exact (
                        normalized TEXT PRIMARY KEY, reference TEXT NOT NULL
                    );
                    CREATE TABLE corpus_entries (
                        id INTEGER PRIMARY KEY, normalized TEXT NOT NULL,
                        reference TEXT NOT NULL
                    );
                    CREATE TABLE corpus_buckets (
                        first_hash INTEGER NOT NULL, second_hash INTEGER NOT NULL,
                        entry_id INTEGER NOT NULL
                    );
                    CREATE INDEX corpus_bucket_lookup
                        ON corpus_buckets(first_hash, second_hash, entry_id);
                """)
            except BaseException:
                self.close()
                raise

    def close(self) -> None:
        if self._database is not None:
            self._database.close()
            self._database = None

    def _check_open(self) -> None:
        if self._disk_backed and self._database is None:
            raise RuntimeError("corpus_index_closed")

    def _exact_reference(self, normalized: str) -> dict | None:
        self._check_open()
        if self._database is None:
            return self._exact.get(normalized)
        row = self._database.execute(
            "SELECT reference FROM corpus_exact WHERE normalized = ?", (normalized,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def exact_reference(self, text: str) -> dict | None:
        """Return the earliest exact exemplar's identity without near matching."""
        return self._exact_reference(comparison_text(text))

    def _set_exact(self, normalized: str, reference: dict, *, replace: bool = False) -> None:
        self._check_open()
        if self._database is None:
            if replace:
                self._exact[normalized] = reference
            else:
                self._exact.setdefault(normalized, reference)
            return
        command = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
        self._database.execute(
            f"{command} INTO corpus_exact(normalized, reference) VALUES (?, ?)",
            (normalized, json.dumps(reference, ensure_ascii=False)),
        )

    @staticmethod
    def _keys(shingles: set[str]) -> tuple[tuple[int, int], ...]:
        hashes = sorted({zlib.crc32(gram.encode("utf-8")) for gram in shingles})[:12]
        return tuple((hashes[index], hashes[index + 1]) for index in range(0, len(hashes) - 1, 2))

    def _find(self, text: str, normalized: str) -> tuple[dict, dict] | None:
        self._check_open()
        exact = self._exact_reference(normalized)
        if exact:
            return ({"reason": "exact_duplicate_corpus", "duplicate_of": exact["id"],
                     "representative_source": exact.get("source_name"), "similarity": 1.0,
                     **({"reference_release": exact["reference_release"]}
                        if "reference_release" in exact else {})}, exact)

        shingles = (_shingles(normalized) if len(normalized) >= NEAR_MIN_CHARS
                    and not _structure_sensitive(text) else set())
        keys = self._keys(shingles) if shingles else ()
        if self._database is None:
            candidates = (self._entries[index]
                          for index in sorted({index for key in keys for index in self._buckets[key]}))
        elif keys:
            placeholders = ", ".join("(?, ?)" for _ in keys)
            parameters = tuple(value for key in keys for value in key)
            candidates = ((text, json.loads(reference)) for text, reference in
                          self._database.execute(
                              "SELECT entries.normalized, entries.reference "
                              "FROM corpus_entries AS entries JOIN ("
                              "SELECT DISTINCT entry_id FROM corpus_buckets "
                              f"WHERE (first_hash, second_hash) IN ({placeholders})"
                              ") AS matches ON matches.entry_id = entries.id "
                              "ORDER BY entries.id", parameters))
        else:
            candidates = ()
        for other_text, other_reference in candidates:
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
        self._check_open()
        normalized = comparison_text(text)
        self._set_exact(normalized, reference)
        shingles = (_shingles(normalized) if len(normalized) >= NEAR_MIN_CHARS
                    and not _structure_sensitive(text) else set())
        keys = self._keys(shingles) if shingles else ()
        if keys:
            if self._database is None:
                index = len(self._entries)
                self._entries.append((normalized, reference))
                for key in keys:
                    self._buckets[key].append(index)
            else:
                cursor = self._database.execute(
                    "INSERT INTO corpus_entries(normalized, reference) VALUES (?, ?)",
                    (normalized, json.dumps(reference, ensure_ascii=False)),
                )
                self._database.executemany(
                    "INSERT INTO corpus_buckets(first_hash, second_hash, entry_id) VALUES (?, ?, ?)",
                    ((first, second, cursor.lastrowid) for first, second in keys),
                )

    def check_and_add(self, text: str, reference: dict) -> dict | None:
        self._check_open()
        normalized = comparison_text(text)
        found = self._find(text, normalized)
        if found:
            self._set_exact(normalized, found[1], replace=True)
            return found[0]
        self.add_reference(text, reference)
        return None


class CorpusClusterSizes:
    """Count packaged duplicate clusters without retaining every root in RAM."""

    def __init__(self, sqlite_path: Path):
        self._database = sqlite3.connect(sqlite_path)
        try:
            self._database.execute("PRAGMA cache_size = -2048")
            self._database.execute("PRAGMA temp_store = FILE")
            self._database.executescript("""
                DROP TABLE IF EXISTS corpus_clusters;
                CREATE TABLE corpus_clusters (
                    root TEXT PRIMARY KEY, size INTEGER NOT NULL, prior_release INTEGER NOT NULL
                );
            """)
        except BaseException:
            self.close()
            raise

    def add(self, row: dict) -> None:
        if row["status"] not in {"eligible", "duplicate"}:
            return
        root = row.get("duplicate_of", row["id"])
        prior = int(row.get("duplicate_scope") == "prior_cpt_release")
        # A prior-release root contributes its verified exemplar once, even
        # when several current rows point to that same external record.
        self._database.execute("""
            INSERT INTO corpus_clusters(root, size, prior_release) VALUES (?, ?, ?)
            ON CONFLICT(root) DO UPDATE SET
                size = size + 1 + CASE WHEN excluded.prior_release = 1
                    AND prior_release = 0 THEN 1 ELSE 0 END,
                prior_release = MAX(prior_release, excluded.prior_release)
        """, (root, 1 + prior, prior))

    def annotate(self, row: dict) -> dict:
        if row["status"] in {"eligible", "duplicate"}:
            root = row.get("duplicate_of", row["id"])
            result = self._database.execute(
                "SELECT size FROM corpus_clusters WHERE root = ?", (root,)
            ).fetchone()
            if result is None:
                raise RuntimeError("missing_corpus_cluster")
            row["duplicate_cluster_size"] = result[0]
        return row

    def close(self) -> None:
        self._database.close()
