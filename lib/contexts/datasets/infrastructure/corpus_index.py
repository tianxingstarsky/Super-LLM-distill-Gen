"""Bounded SQLite storage for the domain's unchanged CPT matching policy."""
from __future__ import annotations
import json
from pathlib import Path
import sqlite3

from lib.domain.corpus_quality import CorpusNearDuplicateIndex as MemoryCorpusIndex


class CorpusNearDuplicateIndex(MemoryCorpusIndex):
    """Use the same policy with memory storage or disposable SQLite indexes."""
    def __init__(self, sqlite_path: Path | None = None):
        super().__init__()
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

    def _candidate_entries(self, keys):
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
        return candidates

    def _store_entry(self, normalized, reference, keys):
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
