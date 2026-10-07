"""Immutable sample commits and checked, portable image archives."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import uuid
import warnings
from zipfile import ZIP_DEFLATED, ZipFile

from filelock import FileLock
from PIL import Image, UnidentifiedImageError

from lib.domain.manual_dataset import (IMAGE_TYPES, MAX_ATTACHMENTS_BYTES, MAX_IMAGE_BYTES,
    MAX_IMAGE_EDGE, MAX_IMAGE_PIXELS, MAX_IMAGES, attachment_name, dataset_name, identifier,
    media_record, sample_record, timestamp, validate_sample)

JSON_LIMIT = 2 * 1024 * 1024
EXPORT_LIMIT = 256 * 1024 * 1024
EXPORT_JSON_LIMIT = 64 * 1024 * 1024
SAMPLE_LIMIT = 100_000
_ID = re.compile(r"[a-f0-9]{32}")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _linked(info) -> bool:
    return (stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)
            or bool(getattr(info, "st_reparse_tag", 0)))


def _check_path(path: Path) -> None:
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if _linked(info) or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1):
            raise ValueError("invalid_manual_dataset_storage")


def _directory(path: Path, *, create: bool = False) -> None:
    _check_path(path)
    if create:
        path.mkdir(parents=True, exist_ok=True)
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise ValueError("manual_dataset_not_found") from None
    if not stat.S_ISDIR(info.st_mode) or _linked(info):
        raise ValueError("invalid_manual_dataset_storage")
    _check_path(path)


def _read(path: Path, limit: int) -> bytes:
    _check_path(path)
    try:
        info = path.lstat()
    except FileNotFoundError:
        raise ValueError("invalid_manual_dataset_storage") from None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit:
        raise ValueError("invalid_manual_dataset_storage")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "rb") as handle:
        opened = os.fstat(handle.fileno())
        if (_linked(opened) or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or (info.st_dev, info.st_ino) != (opened.st_dev, opened.st_ino)
                or opened.st_size > limit):
            raise ValueError("invalid_manual_dataset_storage")
        payload = handle.read(limit + 1)
        after = os.fstat(handle.fileno())
        if (opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise ValueError("invalid_manual_dataset_storage")
    _check_path(path)
    if len(payload) > limit:
        raise ValueError("invalid_manual_dataset_storage")
    return payload


def _json(path: Path) -> dict:
    try:
        value = json.loads(_read(path, JSON_LIMIT))
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise ValueError("invalid_manual_dataset_storage") from None
    if not isinstance(value, dict):
        raise ValueError("invalid_manual_dataset_storage")
    return value


def _serialized(value: dict) -> bytes:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(payload) > JSON_LIMIT:
        raise ValueError("invalid_manual_dataset_storage")
    return payload


def _commit(path: Path, payload: bytes) -> None:
    """A unique temporary file becomes visible only after its complete fsync."""
    _directory(path.parent)
    _check_path(path)
    if path.exists():
        raise ValueError("invalid_manual_dataset_storage")
    temporary = path.with_name("." + uuid.uuid4().hex + ".pending")
    try:
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
                     | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "wb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or _linked(info):
                raise ValueError("invalid_manual_dataset_storage")
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _check_path(path)
        _check_path(temporary)
        os.replace(temporary, path)
        _check_path(path)
    finally:
        temporary.unlink(missing_ok=True)


def _validate_image(name: str, payload: bytes) -> tuple[str, str]:
    attachment_name(name)
    extension = "." + name.rsplit(".", 1)[1].lower()
    expected, mime_type = IMAGE_TYPES[extension]
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_IMAGE_BYTES:
        raise ValueError("invalid_manual_dataset_attachments")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(payload)) as image:
                width, height = image.size
                if (image.format != expected or width < 1 or height < 1
                        or width > MAX_IMAGE_EDGE or height > MAX_IMAGE_EDGE
                        or width * height > MAX_IMAGE_PIXELS or getattr(image, "n_frames", 1) != 1):
                    raise ValueError("invalid_manual_dataset_image")
                image.verify()
            with Image.open(BytesIO(payload)) as image:
                image.load()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError,
            Image.DecompressionBombWarning, Image.DecompressionBombError):
        raise ValueError("invalid_manual_dataset_image") from None
    return (".jpg" if extension == ".jpeg" else extension), mime_type


class ManualDatasetFile:
    def __init__(self, output: Path):
        self.root = Path(os.path.abspath(output)) / "manual_datasets"

    @contextmanager
    def _lock(self, directory: Path):
        _directory(directory, create=True)
        lock_path = directory / ".lock"
        _check_path(lock_path)
        if lock_path.exists() and not stat.S_ISREG(lock_path.lstat().st_mode):
            raise ValueError("invalid_manual_dataset_storage")
        with FileLock(str(lock_path), timeout=10):
            _check_path(lock_path)
            _directory(directory)
            yield

    def _dataset(self, dataset_id: str) -> tuple[Path, dict]:
        path = self.root / identifier(dataset_id)
        _directory(path)
        document = _json(path / "manifest.json")
        try:
            if document.get("version") != 1 or document.get("id") != dataset_id:
                raise ValueError
            name = dataset_name(document["name"])
            created_at = timestamp(document["created_at"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("invalid_manual_dataset_storage") from None
        _directory(path / "samples")
        _directory(path / "media")
        return path, {"id": dataset_id, "name": name, "created_at": created_at}

    def _sample_paths(self, directory: Path) -> list[Path]:
        _directory(directory)
        result = []
        for path in directory.iterdir():
            if path.suffix != ".json" or not _ID.fullmatch(path.stem):
                continue
            _check_path(path)
            if not stat.S_ISREG(path.lstat().st_mode):
                raise ValueError("invalid_manual_dataset_storage")
            result.append(path)
            if len(result) > SAMPLE_LIMIT:
                raise ValueError("manual_dataset_limit_exceeded")
        return sorted(result, key=lambda path: (path.lstat().st_mtime_ns, path.name), reverse=True)

    def list_datasets(self) -> list[dict]:
        _check_path(self.root)
        if not self.root.exists():
            return []
        _directory(self.root)
        rows = []
        for path in self.root.iterdir():
            if not _ID.fullmatch(path.name):
                continue
            path, document = self._dataset(path.name)
            with self._lock(path):
                samples = self._sample_paths(path / "samples")
                updated_at = (datetime.fromtimestamp(samples[0].lstat().st_mtime, timezone.utc).isoformat()
                              if samples else document["created_at"])
                rows.append({"id": document["id"], "name": document["name"],
                             "count": len(samples), "updated_at": updated_at})
        return sorted(rows, key=lambda row: (row["updated_at"], row["id"]), reverse=True)

    def create_dataset(self, name: str) -> str:
        name = dataset_name(name)
        with self._lock(self.root):
            dataset_id = uuid.uuid4().hex
            path = self.root / dataset_id
            _check_path(path)
            pending = self.root / (".pending-" + uuid.uuid4().hex)
            _check_path(pending)
            pending.mkdir(exist_ok=False)
            try:
                _directory(pending / "samples", create=True)
                _directory(pending / "media", create=True)
                _commit(pending / "manifest.json", _serialized({"version": 1, "id": dataset_id,
                                                                "name": name, "created_at": _now()}))
                _check_path(pending)
                _check_path(path)
                if path.exists():
                    raise ValueError("invalid_manual_dataset_storage")
                # Publish the entire initialized directory at once. Interrupted
                # pending directories never participate in the dataset library.
                os.rename(pending, path)
            except BaseException:
                self._discard_pending(pending)
                raise
        return dataset_id

    def _discard_pending(self, pending: Path) -> None:
        """Remove only this operation's known, unpublished files and directories."""
        if pending.parent != self.root or not re.fullmatch(r"\.pending-[a-f0-9]{32}", pending.name):
            raise ValueError("invalid_manual_dataset_storage")
        _check_path(pending)
        if not pending.exists():
            return
        for child, directory in ((pending / "manifest.json", False), (pending / "samples", True),
                                 (pending / "media", True)):
            _check_path(child)
            try:
                child.rmdir() if directory else child.unlink()
            except FileNotFoundError:
                pass
        pending.rmdir()

    def list_samples(self, dataset_id: str, *, limit: int, offset: int) -> list[dict]:
        path, _ = self._dataset(dataset_id)
        with self._lock(path):
            paths = self._sample_paths(path / "samples")[offset:offset + limit]
            return [validate_sample(_json(item), item.stem) for item in paths]

    def append_sample(self, dataset_id: str, *, question: str, answer: str,
                      attachments: list[tuple[str, bytes]], system: str) -> dict:
        path, _ = self._dataset(dataset_id)
        if len(attachments) > MAX_IMAGES or sum(len(payload) for _, payload in attachments) > MAX_ATTACHMENTS_BYTES:
            raise ValueError("invalid_manual_dataset_attachments")
        prepared = [(name, payload, *_validate_image(name, payload)) for name, payload in attachments]
        with self._lock(path):
            if len(self._sample_paths(path / "samples")) >= SAMPLE_LIMIT:
                raise ValueError("manual_dataset_limit_exceeded")
            sample_id = uuid.uuid4().hex
            media = []
            for name, payload, extension, mime_type in prepared:
                media_id = uuid.uuid4().hex
                media.append({"id": media_id, "name": name, "mime_type": mime_type, "kind": "image",
                              "path": f"media/{media_id}{extension}",
                              "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload)})
            row = sample_record(sample_id, question, answer, system, media, _now())
            committed = []
            try:
                for item, (_, payload, _, _) in zip(media, prepared):
                    media_path = path / PurePosixPath(item["path"])
                    _commit(media_path, payload)
                    committed.append(media_path)
                    index_path = path / "media" / (item["id"] + ".json")
                    _commit(index_path, _serialized({"version": 1, "sample_id": sample_id, **item}))
                    committed.append(index_path)
                # This file is the commit marker. Interrupted uploads never appear as samples.
                _commit(path / "samples" / (sample_id + ".json"), _serialized(row))
            except BaseException:
                for item in reversed(committed):
                    _check_path(item)
                    item.unlink(missing_ok=True)
                raise
            return row

    def _media(self, path: Path, media_id: str) -> tuple[dict, bytes]:
        identifier(media_id)
        index_path = path / "media" / (media_id + ".json")
        _check_path(index_path)
        if not index_path.exists():
            raise ValueError("manual_media_not_found")
        index = _json(index_path)
        try:
            if index.get("version") != 1 or index.get("id") != media_id:
                raise ValueError
            sample_id = identifier(index["sample_id"])
            media = media_record(index)
            row = validate_sample(_json(path / "samples" / (sample_id + ".json")), sample_id)
            if media not in row["media"]:
                raise ValueError
        except (KeyError, ValueError, TypeError):
            raise ValueError("invalid_manual_dataset_storage") from None
        payload = _read(path / PurePosixPath(media["path"]), MAX_IMAGE_BYTES)
        if len(payload) != media["size"] or hashlib.sha256(payload).hexdigest() != media["sha256"]:
            raise ValueError("manual_media_integrity_failed")
        _validate_image(media["name"], payload)
        return media, payload

    def read_media(self, dataset_id: str, media_id: str) -> bytes:
        path, _ = self._dataset(dataset_id)
        with self._lock(path):
            return self._media(path, media_id)[1]

    def export_dataset(self, dataset_id: str) -> bytes:
        path, dataset = self._dataset(dataset_id)
        with self._lock(path):
            paths = list(reversed(self._sample_paths(path / "samples")))
            jsonl = BytesIO()
            archive = BytesIO()
            total = 0
            media_count = 0
            seen_media = set()
            with ZipFile(archive, "w", compression=ZIP_DEFLATED) as package:
                for item in paths:
                    row = validate_sample(_json(item), item.stem)
                    line = _serialized(row) + b"\n"
                    total += len(line)
                    if total > EXPORT_LIMIT or jsonl.tell() + len(line) > EXPORT_JSON_LIMIT:
                        raise ValueError("manual_dataset_export_too_large")
                    jsonl.write(line)
                    for media in row["media"]:
                        if media["id"] in seen_media:
                            raise ValueError("invalid_manual_dataset_storage")
                        seen_media.add(media["id"])
                        checked, payload = self._media(path, media["id"])
                        if checked != media:
                            raise ValueError("invalid_manual_dataset_storage")
                        total += len(payload)
                        if total > EXPORT_LIMIT:
                            raise ValueError("manual_dataset_export_too_large")
                        package.writestr(media["path"], payload)
                        media_count += 1
                package.writestr("samples.jsonl", jsonl.getvalue())
                package.writestr("manifest.json", _serialized({"version": 1, **dataset,
                    "exported_at": _now(), "count": len(paths), "media_count": media_count,
                    "origin": "manual", "review_status": "unreviewed", "format": "messages",
                    "samples": "samples.jsonl", "media_directory": "media"}))
            result = archive.getvalue()
            if len(result) > EXPORT_LIMIT:
                raise ValueError("manual_dataset_export_too_large")
            return result
