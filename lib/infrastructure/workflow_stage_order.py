"""Bounded-memory ordering for completed concurrent stage items.

The first item can remain slow while every later item finishes. Completed
records therefore live in a temporary SQLite spool rather than an in-memory
dictionary. Durable item checkpoints remain the authority for recovery.
"""
from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import tempfile

from lib.domain.workflow_quality import canonical


class StageResultOrder:
    def __init__(self, directory: Path, stage: str):
        descriptor, name = tempfile.mkstemp(prefix=f".{stage}.order-", suffix=".sqlite3", dir=directory)
        os.close(descriptor)
        self.path = Path(name)
        self.connection = sqlite3.connect(self.path)
        # This buffer is disposable; only the checkpoint and final stage file
        # are recovery records. Bound SQLite's page cache independently of how
        # many completed items are waiting behind the first unfinished item.
        self.connection.execute("PRAGMA journal_mode=OFF")
        self.connection.execute("PRAGMA cache_size=-1024")
        self.connection.execute("""CREATE TABLE rows (
            item_index INTEGER NOT NULL, row_index INTEGER NOT NULL, payload TEXT,
            PRIMARY KEY (item_index, row_index)) WITHOUT ROWID""")
        self.next_index = 0

    def __enter__(self):
        return self

    def __exit__(self, *exception):
        self.connection.close()
        self.path.unlink(missing_ok=True)

    def append(self, index, rows, preview):
        for position, row in enumerate(rows):
            payload = canonical(row) + "\n"
            self.connection.execute("INSERT INTO rows VALUES (?, ?, ?)", (index, position, payload))
            preview.write(canonical({"item_index": index, "record": row}) + "\n")
        # Empty outputs must also advance the canonical input order.
        self.connection.execute("INSERT INTO rows VALUES (?, -1, NULL)", (index,))
        self.connection.commit()
        preview.flush()

    def flush(self, handle):
        start = self.next_index
        while self.connection.execute(
                "SELECT 1 FROM rows WHERE item_index=? AND row_index=-1", (self.next_index,)).fetchone():
            for (payload,) in self.connection.execute(
                    "SELECT payload FROM rows WHERE item_index=? AND row_index>=0 ORDER BY row_index",
                    (self.next_index,)):
                handle.write(payload)
            self.connection.execute("DELETE FROM rows WHERE item_index=?", (self.next_index,))
            self.next_index += 1
        if self.next_index != start:
            self.connection.commit()
            handle.flush()
            return True
        return False
