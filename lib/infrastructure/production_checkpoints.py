"""Integrity checked, durable model results without one file per model call."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
from contextlib import closing

from lib.domain.workflow_quality import canonical


class ProductionCheckpoints:
    def __init__(self, path):
        self.path = Path(path)
        if self.path.is_symlink():
            raise ValueError("checkpoint_integrity_error")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection, connection:
            connection.execute("CREATE TABLE IF NOT EXISTS checkpoints "
                               "(stage TEXT, key TEXT, payload TEXT NOT NULL, sha256 TEXT NOT NULL, "
                               "PRIMARY KEY(stage, key))")

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA cache_size=-2048")
        return connection

    def get(self, stage, key):
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT payload, sha256 FROM checkpoints WHERE stage=? AND key=?",
                                     (stage, key)).fetchone()
        if row is None:
            return False, None
        if hashlib.sha256(row[0].encode("utf-8")).hexdigest() != row[1]:
            raise ValueError("checkpoint_integrity_error")
        return True, json.loads(row[0])

    def put(self, stage, key, data):
        payload = canonical(data)
        with closing(self._connect()) as connection, connection:
            connection.execute("INSERT OR REPLACE INTO checkpoints VALUES (?, ?, ?, ?)",
                               (stage, key, payload, hashlib.sha256(payload.encode("utf-8")).hexdigest()))

    def delete(self, stage, key):
        with closing(self._connect()) as connection, connection:
            connection.execute("DELETE FROM checkpoints WHERE stage=? AND key=?", (stage, key))
