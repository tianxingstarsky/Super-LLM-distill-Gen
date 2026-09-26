"""Indexed normalization of selected JSONL examples without retaining all rows."""
from pathlib import Path

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
