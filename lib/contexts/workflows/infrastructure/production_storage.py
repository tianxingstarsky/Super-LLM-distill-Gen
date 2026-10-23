"""Disk-backed source windows and immutable receipt-selected collections."""
from __future__ import annotations
import hashlib
import json
import sqlite3
from pathlib import Path
from lib.domain.workflow_quality import canonical
from ..domain.scale import generation_variant
from lib.infrastructure.workflow_rows import WorkflowRows

def _digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _hash_file(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _production_collection_file(directory, receipt, target):
    """A receipt selects one immutable file; legacy receipts select target.jsonl."""
    files = receipt.get("files", {})
    if not isinstance(files, dict) or set(files) - set(receipt["rows"]):
        raise ValueError("production_round_integrity_error")
    name = files.get(target, f"{target}.jsonl")
    if (not isinstance(name, str) or Path(name).name != name or
            not name.startswith(target + ".") or not name.endswith(".jsonl") or name not in receipt["sha256"]):
        raise ValueError("production_round_integrity_error")
    return directory / f"round-{receipt['round']:06d}" / name


class ProductionInputs:
    """Random-access source units on disk; source text is never replicated per round."""
    def __init__(self, path, units):
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA cache_size=-2048")
        self.connection.executescript("DROP TABLE IF EXISTS units; DROP TABLE IF EXISTS documents; "
            "CREATE TABLE units (idx INTEGER PRIMARY KEY, payload TEXT); "
            "CREATE TABLE documents (idx INTEGER PRIMARY KEY, unit_idx INTEGER);")
        self.count = self.documents = 0
        for unit in units:
            self.connection.execute("INSERT INTO units VALUES (?, ?)", (self.count, canonical(unit)))
            if unit.get("kind") == "document":
                self.connection.execute("INSERT INTO documents VALUES (?, ?)", (self.documents, self.count))
                self.documents += 1
            self.count += 1
        self.connection.commit()

    def close(self):
        self.connection.close()

    def rows(self, offset, count, *, expand):
        stop = offset + count if expand and self.documents else min(offset + count, self.count)
        for index in range(offset, stop):
            if index < self.count:
                row = self.connection.execute("SELECT payload FROM units WHERE idx=?", (index,)).fetchone()
                yield json.loads(row[0])
            else:
                number = (index - self.count) % self.documents
                row = self.connection.execute("SELECT payload FROM units JOIN documents ON units.idx=documents.unit_idx "
                                              "WHERE documents.idx=?", (number,)).fetchone()
                yield generation_variant(json.loads(row[0]), index, self.documents)


class ProductionCollections:
    def __init__(self, directory, receipts, target):
        self.directory, self.receipts, self.target = directory, receipts, target
        self.count = sum(item["rows"].get(target, 0) for item in receipts)

    def __len__(self):
        return self.count

    def __iter__(self):
        for receipt in self.receipts:
            if self.target in receipt["rows"]:
                path = _production_collection_file(self.directory, receipt, self.target)
                yield from WorkflowRows(path, receipt["rows"][self.target])
