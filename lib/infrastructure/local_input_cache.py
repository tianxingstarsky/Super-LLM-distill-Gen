"""Persistent content-addressed source cache shared by the local console."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
import tempfile

from filelock import FileLock

from lib import workspace


def _assert_unlinked(path: Path) -> None:
    """Reject symbolic links, Windows junctions, and other reparse points."""
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0)
                or (stat.S_ISREG(info.st_mode) and info.st_nlink > 1)):
            raise ValueError("linked_upload_cache_path")


class FilesystemLocalInputCacheDriver:
    def __init__(self, root: Path | None = None):
        seeds = workspace.SEEDS_DIR if root is None else Path(root) / "data" / "seeds"
        self._cache = Path(seeds).absolute() / "uploads"
        self._data = self._cache.parent.parent

    def _check_directory(self, path: Path) -> None:
        _assert_unlinked(path)
        if path.exists() and not path.is_dir():
            raise ValueError("invalid_upload_cache_path")

    def _check_existing(self, path: Path, content: bytes) -> bool:
        _assert_unlinked(path)
        if not path.exists():
            return False
        if not path.is_file():
            raise ValueError("invalid_upload_cache_path")
        if path.stat().st_size != len(content) or path.read_bytes() != content:
            raise ValueError("upload_cache_content_mismatch")
        return True

    def _targets(self, uploads: tuple[tuple[str, bytes], ...]) -> list[tuple[Path, str, bytes]]:
        """Resolve aliases by content before writing anything from this batch."""
        targets: list[tuple[Path, str, bytes]] = []
        chosen: dict[str, Path] = {}
        for name, content in uploads:
            digest = hashlib.sha256(content).hexdigest()
            directory = self._cache / digest
            self._check_directory(directory)
            path = directory / name
            if path.parent != directory or path.name != name:
                raise ValueError("invalid_upload_name")
            if directory.exists():
                existing = sorted(directory.iterdir(), key=lambda item: item.name)
                for candidate in existing:
                    self._check_existing(candidate, content)
                if existing:
                    path = existing[0]
            path = chosen.setdefault(digest, path)
            self._check_existing(path, content)
            targets.append((path, name, content))
        return targets

    def store(self, uploads: tuple[tuple[str, bytes], ...]) -> list[dict]:
        # Validate the existing tree for the whole batch before creating files.
        self._check_directory(self._data)
        self._check_directory(self._cache)
        self._targets(uploads)
        lock_path = self._data / ".upload-cache.lock"
        _assert_unlinked(lock_path)
        if lock_path.exists() and not lock_path.is_file():
            raise ValueError("invalid_upload_cache_path")
        self._data.mkdir(parents=True, exist_ok=True)
        with FileLock(str(lock_path), timeout=10):
            # Another console session may have filled the cache while we waited.
            self._check_directory(self._cache)
            targets = self._targets(uploads)
            for path, _, content in targets:
                if self._check_existing(path, content):
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                _assert_unlinked(path.parent)
                temporary: Path | None = None
                try:
                    # Staging outside seeds keeps partial files out of source inventories.
                    with tempfile.NamedTemporaryFile("wb", dir=self._data,
                                                     prefix=".upload-", suffix=".tmp",
                                                     delete=False) as output:
                        temporary = Path(output.name)
                        output.write(content)
                        output.flush()
                        os.fsync(output.fileno())
                    _assert_unlinked(path)
                    os.replace(temporary, path)
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
        return [{"path": str(path), "name": name, "size": len(content)}
                for path, name, content in targets]
