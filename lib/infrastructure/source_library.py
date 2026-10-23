"""Persistent, content-addressed source originals and complete parser outputs.

Generation targets and candidate counts do not belong in a parsing key. Writers
hold an operating-system file lock while producing a key, and publish a manifest
only after the entire stream has finished successfully. Valid model page results
are separate entries so an interrupted document does not repeat paid work.
"""
from __future__ import annotations

from datetime import datetime, timezone
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time

from filelock import FileLock, Timeout

from lib.io_utils import atomic_json
from lib.infrastructure.source_snapshot import snapshot_source


SOURCE_LIBRARY_VERSION = 1
MODEL_PARSE_CHARS = 8000
SOURCE_PROGRESS_INTERVAL_SECONDS = .5
_SHA = re.compile(r"^[0-9a-f]{64}$")
_SOURCE_HASHES = {}
_SOURCE_HASH_LOCK = threading.Lock()


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _hash_file(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def _unlinked(path):
    for candidate in (Path(path), *Path(path).parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0)
                or (stat.S_ISREG(info.st_mode) and info.st_nlink > 1)):
            raise ValueError("linked_source_library_path")


def _sha(value):
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise ValueError("invalid_source_library_id")
    return value


def native_parse_signature(*, source_processing_version=2, pdf_pages=True):
    return {"mode": "native", "parser_version": SOURCE_LIBRARY_VERSION,
            "source_processing_version": source_processing_version,
            "pdf_pages": bool(pdf_pages)}


class SourceLibrary:
    """One durable library beneath a workspace's output directory."""

    def __init__(self, output):
        self.path = Path(output).absolute() / ".source-library"
        _unlinked(self.path)

    def _lock(self, key):
        _sha(key)
        path = self.path / "locks" / (key + ".lock")
        _unlinked(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        return FileLock(str(path), timeout=-1)

    @contextmanager
    def _locked(self, key, check_cancel=None):
        lock = self._lock(key)
        while True:
            if check_cancel:
                check_cancel()
            try:
                lock.acquire(timeout=.2)
                break
            except Timeout:
                continue
        try:
            yield
        finally:
            lock.release()

    def register_source(self, path, sha256, name=None):
        """Keep a verified original independently of any run's input directory."""
        path = Path(path)
        _sha(sha256)
        suffix = path.suffix.lower()
        if not re.fullmatch(r"\.[a-z0-9]{1,12}", suffix):
            raise ValueError("invalid_source_library_format")
        name = Path(name or path.name).name[:240]
        source_id = _digest([sha256, suffix])
        folder = self.path / "sources" / source_id
        original = folder / ("original" + suffix)
        manifest = folder / "source.json"
        _unlinked(path)
        _unlinked(folder)
        with self._lock(source_id):
            folder.mkdir(parents=True, exist_ok=True)
            _unlinked(original)
            _unlinked(manifest)
            if _hash_file(path) != sha256:
                raise ValueError("source_snapshot_changed")
            if original.exists():
                if _hash_file(original) != sha256:
                    raise ValueError("source_library_original_changed")
            else:
                pending = original.with_name("." + original.name + ".pending")
                _unlinked(pending)
                pending.unlink(missing_ok=True)  # A dead writer cannot still own this key's lock.
                copied = snapshot_source(path, original, 50 * 1024 * 1024)
                if copied["sha256"] != sha256:
                    original.unlink(missing_ok=True)
                    raise ValueError("source_snapshot_changed")
            saved = json.loads(manifest.read_text(encoding="utf-8")) if manifest.exists() else {}
            names = list(dict.fromkeys([*saved.get("names", []), name]))[-100:]
            row = {"version": SOURCE_LIBRARY_VERSION, "id": source_id, "sha256": sha256,
                   "suffix": suffix, "name": names[0], "names": names,
                   "bytes": original.stat().st_size,
                   "created_at": saved.get("created_at", _now()), "last_seen_at": _now(),
                   "original": original.name}
            atomic_json(manifest, row)
            return row

    def source_path(self, source_id):
        folder = self.path / "sources" / _sha(source_id)
        _unlinked(folder)
        metadata = json.loads((folder / "source.json").read_text(encoding="utf-8"))
        name = metadata.get("original")
        if not isinstance(name, str) or Path(name).name != name:
            raise ValueError("source_library_integrity_error")
        path = folder / name
        _unlinked(path)
        if _hash_file(path) != metadata.get("sha256"):
            raise ValueError("source_library_original_changed")
        return path

    @staticmethod
    def parse_id(source, signature):
        return _digest({"library_version": SOURCE_LIBRARY_VERSION,
                        "source_id": source["id"], "signature": signature})

    def _read_manifest(self, folder, source, signature):
        manifest, data = folder / "parse.json", folder / "parts.jsonl"
        _unlinked(manifest)
        _unlinked(data)
        if not manifest.exists():
            return None
        try:
            row = json.loads(manifest.read_text(encoding="utf-8"))
            expected = self.parse_id(source, signature)
            if (not isinstance(row, dict) or row.get("id") != expected
                    or row.get("source_id") != source["id"] or row.get("signature") != signature
                    or row.get("version") != SOURCE_LIBRARY_VERSION
                    or row.get("status") != "complete" or type(row.get("parts")) is not int
                    or row["parts"] < 0 or not data.is_file()
                    or _hash_file(data) != row.get("parts_sha256")):
                raise ValueError("source_library_integrity_error")
            return row
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise ValueError("source_library_integrity_error") from error

    def iter_parts(self, source, signature, build, *, valid=None, check_cancel=None, on_status=None,
                   cached_valid=None, cache_incomplete=False):
        """Stream all parts; closing or failing the producer never publishes a hit."""
        key = self.parse_id(source, signature)
        folder = self.path / "parses" / source["id"] / key
        _unlinked(folder)
        with self._locked(key, check_cancel):
            if check_cancel:
                check_cancel()
            self.source_path(source["id"])
            cached = self._read_manifest(folder, source, signature)
            if cached is not None and cached.get("evidence_only") and not cache_incomplete:
                cached=None
                (folder/"parse.json").unlink()
            if cached is not None and cached_valid is not None:
                # A stricter consumer can retire only obsolete invalid results
                # while preserving good expensive readings under the same key.
                # Verify before yielding anything: a partial cache hit cannot
                # hide the later page that must be rebuilt.
                with (folder / "parts.jsonl").open(encoding="utf-8") as handle:
                    for line in handle:
                        if check_cancel:
                            check_cancel()
                        if not cached_valid(json.loads(line)):
                            cached = None
                            (folder / "parse.json").unlink()
                            break
            if cached is not None:
                if on_status:
                    on_status({**cached, "cached": True})
                count = 0
                with (folder / "parts.jsonl").open(encoding="utf-8") as handle:
                    for line in handle:
                        if check_cancel:
                            check_cancel()
                        row = json.loads(line)
                        count += 1
                        yield row
                if count != cached["parts"]:
                    raise ValueError("source_library_integrity_error")
                return
            folder.mkdir(parents=True, exist_ok=True)
            if on_status:
                on_status({"id": key, "source_id": source["id"], "mode": signature["mode"],
                           "status": "parsing", "cached": False, "parts": 0, "text_chars": 0})
            fd, temporary = tempfile.mkstemp(dir=folder, prefix=".parts-", suffix=".pending")
            pending = Path(temporary)
            count, text_chars, usable_parts, requires_vision, empty_parts, successful = 0, 0, 0, 0, 0, True
            last_progress = None
            last_row = None
            def progress(status, row=None):
                if on_status is None:
                    return
                note = {"id": key, "source_id": source["id"], "mode": signature["mode"],
                        "status": status, "cached": False, "parts": count, "text_chars": text_chars,
                        "usable_parts": usable_parts, "requires_vision": requires_vision, "empty_parts": empty_parts}
                if row is not None:
                    note.update({name: row[name] for name in ("location", "parse_segment") if name in row})
                on_status(note)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    iterator = iter(build())
                    try:
                        for row in iterator:
                            if check_cancel:
                                check_cancel()
                            if not isinstance(row, dict):
                                raise ValueError("invalid_source_library_part")
                            last_row = row
                            if valid is not None and not valid(row):
                                successful = False
                            handle.write(_canonical(row) + "\n")
                            count += 1
                            text_chars += len(row.get("text", "")) if isinstance(row.get("text"), str) else 0
                            if row.get("requires_vision"):
                                requires_vision += 1
                            elif isinstance(row.get("text"), str):
                                if row["text"].strip() and row.get("status") not in {"quarantined", "skipped"}:
                                    usable_parts += 1
                                else:
                                    empty_parts += 1
                            elif isinstance(row.get("record"), dict) and row["record"]:
                                usable_parts += 1
                            moment = time.monotonic()
                            if last_progress is None or moment - last_progress >= SOURCE_PROGRESS_INTERVAL_SECONDS:
                                # A completed reading is visible before the next
                                # model call, without writing state per token or
                                # for every tiny JSONL record in a large import.
                                progress("parsing", row)
                                last_progress = moment
                            yield row
                    finally:
                        close = getattr(iterator, "close", None)
                        if close:
                            close()
                    handle.flush()
                    os.fsync(handle.fileno())
                if not successful and not cache_incomplete:
                    progress("incomplete", last_row)
                    return
                self.source_path(source["id"])
                record = {"version": SOURCE_LIBRARY_VERSION, "id": key, "source_id": source["id"],
                          "source_sha256": source["sha256"], "signature": signature,
                          "mode": signature["mode"], "status": "complete", "parts": count,
                          "text_chars": text_chars, "usable_parts": usable_parts,
                          "requires_vision": requires_vision, "empty_parts": empty_parts,
                          "parts_sha256": _hash_file(pending),
                          "created_at": _now()}
                if not successful:record["evidence_only"]=True
                if last_row is not None:
                    record.update({name: last_row[name] for name in ("location", "parse_segment") if name in last_row})
                destination = folder / "parts.jsonl"
                _unlinked(destination)
                pending.replace(destination)
                atomic_json(folder / "parse.json", record)
                if on_status:
                    on_status({**record, "cached": False})
            except BaseException as error:
                progress("interrupted" if isinstance(error, GeneratorExit) else "failed", last_row)
                raise
            finally:
                pending.unlink(missing_ok=True)

    def cached_value(self, source, signature, key, action, *, valid, refresh_invalid=False):
        """Save successful individual model readings, including across restarts."""
        parse_key = self.parse_id(source, signature)
        value_key = _digest([parse_key, key])
        path = self.path / "readings" / source["id"] / parse_key / (value_key + ".json")
        _unlinked(path)
        with self._lock(value_key):
            if path.exists():
                try:
                    saved = json.loads(path.read_text(encoding="utf-8"))
                    value = saved["data"]
                    if saved.get("sha256") != _digest(value):
                        raise ValueError("source_library_integrity_error")
                    if valid(value):
                        return value, True
                    if not refresh_invalid:
                        raise ValueError("source_library_integrity_error")
                    path.unlink()
                except (KeyError, TypeError, json.JSONDecodeError) as error:
                    raise ValueError("source_library_integrity_error") from error
            value = action()
            if valid(value):
                atomic_json(path, {"data": value, "sha256": _digest(value)})
            return value, False

    def native_parts(self, source, *, source_processing_version=2, pdf_pages=True,
                     check_cancel=None, on_status=None, preflight=None, text_reader=None):
        """Shared extraction used by workflow, local preview and RAG indexing."""
        signature = native_parse_signature(source_processing_version=source_processing_version,
                                           pdf_pages=pdf_pages)
        def build():
            path = self.source_path(source["id"])
            if preflight:
                preflight(path)
            if pdf_pages and path.suffix == ".pdf":
                from lib.infrastructure.document_text import text_parts
                yield from text_parts(path, source_processing_version=source_processing_version)
            else:
                from lib.doc2corpus import import_text
                text = (text_reader(path) if text_reader is not None else
                        import_text(path, source_processing_version=source_processing_version))
                yield {"location": "document", "text": text, "requires_vision": False}
        yield from self.iter_parts(source, signature, build, check_cancel=check_cancel,
                                   on_status=on_status)

    def list_parses(self, source_id, *, limit=100):
        _sha(source_id)
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("invalid_source_library_limit")
        folder = self.path / "parses" / source_id
        _unlinked(folder)
        rows = []
        if not folder.exists():
            return rows
        for item in sorted(folder.iterdir(), key=lambda path: path.name):
            if len(rows) >= limit:
                break
            manifest = item / "parse.json"
            _unlinked(manifest)
            if not manifest.is_file():
                continue
            row = json.loads(manifest.read_text(encoding="utf-8"))
            if row.get("status") == "complete":
                # Only public parser metadata; no source contents or credentials.
                public = {key: row[key] for key in ("id", "source_id", "mode", "status", "parts",
                          "text_chars", "usable_parts", "requires_vision", "empty_parts",
                          "created_at", "parts_sha256") if key in row}
                signature = row.get("signature", {})
                public.update(parser_version=signature.get("parser_version", SOURCE_LIBRARY_VERSION),
                              model=signature.get("model", {}).get("model"),
                              prompt_sha256=signature.get("prompt_sha256"))
                rows.append(public)
        return sorted(rows, key=lambda row: row.get("created_at", ""), reverse=True)

    def list_sources(self, *, limit=100, offset=0):
        if type(limit) is not int or not 1 <= limit <= 1000 or type(offset) is not int or offset < 0:
            raise ValueError("invalid_source_library_limit")
        folder = self.path / "sources"
        _unlinked(folder)
        if not folder.exists():
            return []
        rows = []
        for item in sorted(folder.iterdir(), key=lambda path: path.name)[offset:offset + limit]:
            manifest = item / "source.json"
            _unlinked(manifest)
            if manifest.is_file():
                row = json.loads(manifest.read_text(encoding="utf-8"))
                rows.append({**row, "parses": self.list_parses(row["id"])})
        return rows

    def source_statuses(self, sha256s, *, suffix=None):
        """Bounded file-hash lookup for an existing source inventory."""
        wanted = {_sha(value) for value in sha256s}
        if len(wanted) > 5000:
            raise ValueError("invalid_source_library_limit")
        result = {value: {"sha256": value, "status": "unparsed", "parses": 0,
                          "parts": 0, "text_chars": 0, "usable_parts": 0,
                          "requires_vision": 0, "empty_parts": 0, "modes": []} for value in wanted}
        folder = self.path / "sources"
        _unlinked(folder)
        if not folder.exists():
            return result
        candidates = (folder / _digest([sha256, suffix.lower()]) for sha256 in sorted(wanted)) if suffix is not None else folder.iterdir()
        for index, item in enumerate(candidates):
            if index >= 5000:
                break
            manifest = item / "source.json"
            _unlinked(manifest)
            if not manifest.is_file():
                continue
            row = json.loads(manifest.read_text(encoding="utf-8"))
            sha256 = row.get("sha256")
            if sha256 not in wanted or (suffix is not None and row.get("suffix") != suffix.lower()):
                continue
            parses = self.list_parses(row["id"])
            if not parses:
                continue
            chosen = next((parse for parse in parses if parse["mode"] in {"model", "vision"}), parses[0])
            usable = chosen.get("usable_parts", int(chosen["text_chars"] > 0))
            needs_vision = chosen.get("requires_vision", 0)
            status = "requires_vision" if needs_vision else "parsed" if usable else "empty"
            result[sha256] = {"sha256": sha256, "source_id": row["id"], "status": status,
                              "parses": len(parses), "parts": chosen["parts"],
                              "text_chars": chosen["text_chars"],
                              "usable_parts": usable, "requires_vision": needs_vision,
                              "empty_parts": chosen.get("empty_parts", 0),
                              "modes": sorted({parse["mode"] for parse in parses}),
                              "model": chosen.get("model"), "parser_version": chosen.get("parser_version"),
                              "prompt_sha256": chosen.get("prompt_sha256"),
                              "last_parsed_at": chosen["created_at"]}
        return result

    def source_status(self, sha256, *, suffix=None):
        return self.source_statuses([sha256], suffix=suffix)[_sha(sha256)]

    def describe_source(self, path):
        """Hash only a selected original, retaining a bounded stat-keyed digest cache."""
        path = Path(path).absolute()
        _unlinked(path)
        before = path.stat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > 50 * 1024 * 1024:
            raise ValueError("invalid_source_library_original")
        stamp = (before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino)
        cache_key = str(path)
        with _SOURCE_HASH_LOCK:
            saved = _SOURCE_HASHES.get(cache_key)
        sha256 = saved[1] if saved is not None and saved[0] == stamp else _hash_file(path)
        after = path.stat()
        if (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino) != stamp:
            raise ValueError("source_snapshot_changed")
        with _SOURCE_HASH_LOCK:
            if len(_SOURCE_HASHES) >= 256 and cache_key not in _SOURCE_HASHES:
                _SOURCE_HASHES.pop(next(iter(_SOURCE_HASHES)))
            _SOURCE_HASHES[cache_key] = (stamp, sha256)
        return {"name": path.name, "bytes": before.st_size,
                **self.source_status(sha256, suffix=path.suffix)}
