"""Replayable disk-backed rows keep stage and export memory bounded."""
from __future__ import annotations

import json
from pathlib import Path

from lib.domain.workflow_quality import canonical


class WorkflowRows:
    def __init__(self, path: Path, count: int, *, predicate=None, transform=None, limit=None):
        self.path, self.count = Path(path), count
        self.predicate, self.transform = predicate, transform
        self.limit = limit

    def __len__(self):
        return self.count

    def __iter__(self):
        if self.limit == 0:
            return
        emitted = 0
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if self.predicate is None or self.predicate(row):
                    yield self.transform(row) if self.transform else row
                    emitted += 1
                    if self.limit is not None and emitted >= self.limit:
                        break

    def __getitem__(self, index):
        if isinstance(index, slice):
            from itertools import islice
            return list(islice(self, index.start or 0, index.stop, index.step or 1))
        if index < 0:
            index += len(self)
        for position, row in enumerate(self):
            if position == index:
                return row
        raise IndexError(index)

    def eligible(self, count: int):
        return WorkflowRows(self.path, count, predicate=lambda row: row["status"] == "eligible")

    def ready(self, count: int, limit: int):
        return WorkflowRows(self.path, min(count, limit),
                            predicate=lambda row: row['status'] == 'ready', limit=limit)


class RowSpool(WorkflowRows):
    def __init__(self, path: Path):
        super().__init__(path, 0)
        self._handle = self.path.open("w", encoding="utf-8")

    def append(self, row):
        self._handle.write(canonical(row) + "\n")
        self.count += 1

    def close(self):
        self._handle.close()

    def __iter__(self):
        if not self._handle.closed:
            self._handle.flush()
        return super().__iter__()


def write_jsonl(path: Path, rows) -> None:
    temporary = path.with_name("." + path.name + ".pending")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(canonical(row) + "\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_json_array(path: Path, rows) -> None:
    temporary = path.with_name("." + path.name + ".pending")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write("[")
            for index, row in enumerate(rows):
                if index:
                    handle.write(",")
                handle.write(canonical(row))
            handle.write("]")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
