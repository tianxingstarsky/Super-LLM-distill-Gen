"""Indexed normalization of selected JSONL examples without retaining all rows."""
from pathlib import Path
import json

from lib.domain.review_edit import normalize_sample
from lib.infrastructure.jsonl_preview import _identity, read_rows, row_count


class SamplePreview:
    def __init__(self, path):
        self.path = Path(path)
        self.identity = _identity(self.path)
        self.count = row_count(self.path)
        self._check()

    def _check(self):
        if _identity(self.path) != self.identity:
            raise ValueError("preview_file_changed")

    def __len__(self):
        self._check()
        return self.count

    def __iter__(self):
        """Sequential import uses one row at a time and checks the source lease."""
        self._check()
        count = 0
        with self.path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    row = normalize_sample(json.loads(line))
                except (ValueError, TypeError) as error:
                    raise ValueError(f"{self.path}:{number}: {error}") from error
                count += 1
                yield row
        self._check()
        if count != self.count:
            raise ValueError("preview_file_changed")

    def __getitem__(self, index):
        if not isinstance(index, int):
            raise TypeError("Preview requires an individual sample index")
        self._check()
        if index < 0:
            index += self.count
        if not 0 <= index < self.count:
            raise IndexError(index)
        try:
            rows = read_rows(self.path, index, 1)
        except ValueError as error:
            raise ValueError(f"{self.path}: record {index + 1}: {error}") from error
        self._check()
        if not rows or not isinstance(rows[0], dict):
            raise ValueError(f"{self.path}: record {index + 1}: expected an object")
        return normalize_sample(rows[0])
