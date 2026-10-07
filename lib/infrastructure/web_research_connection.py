"""Root-scoped local Brave credentials; never part of a workflow recipe.

An explicit local override wins over DATAFORGE_BRAVE_SEARCH_API_KEY. Removing
the override restores the environment. An unreadable local file fails closed;
it never silently selects a different account. Windows protects key bytes with
current-user DPAPI and root-bound entropy; POSIX files are owner-only.
"""
from __future__ import annotations

import base64
from collections import OrderedDict
from contextlib import nullcontext
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import uuid

from filelock import FileLock

from lib import workspace
from lib.io_utils import _replace_state


KEY_ENV = "DATAFORGE_BRAVE_SEARCH_API_KEY"
MAX_CONNECTION_BYTES = 16 * 1024
MAX_KEY_CHARS = 4096
_REVISION = re.compile(r"[0-9a-f]{32}\Z")
_ENV_LOCK = threading.Lock()
_ENV_REVISIONS: OrderedDict[str, tuple[str, str]] = OrderedDict()


class _InvalidConnection(ValueError):
    def __init__(self, key_source: str):
        super().__init__("web_search_connection_invalid")
        self.key_source = key_source


@dataclass(frozen=True)
class _Connection:
    api_key: str = field(repr=False)
    key_source: str
    revision: str

    def capabilities(self) -> dict:
        return {"brave_configured": bool(self.api_key), "key_source": self.key_source,
                "connection_revision": self.revision}


def _root(root: Path) -> Path:
    return Path(os.path.abspath(os.fspath(root)))


def _guard(root: Path) -> None:
    # The private distribution supplies this existing fail-closed boundary.
    # Do not replace it with a second team identity or workspace mechanism.
    guard = getattr(workspace, "_require_shared_workspace_access", None)
    if guard is not None:
        guard(root)


def _unlinked(path: Path) -> None:
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0)
                or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)):
            raise ValueError("web_search_connection_invalid")


def _key(value: str, *, empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError("web_search_connection_invalid")
    value = value.strip()
    if (len(value) > MAX_KEY_CHARS or (not value and not empty)
            or any(not 33 <= ord(char) <= 126 for char in value)):
        raise ValueError("web_search_connection_invalid")
    return value


def _protect(data: bytes, root: Path, *, decrypt: bool = False) -> bytes:
    """DPAPI uses the current Windows account, without prompting or logging."""
    if os.name != "nt":
        return data
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    def blob(value):
        buffer = ctypes.create_string_buffer(value)
        return Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer

    source, source_buffer = blob(data)
    entropy, entropy_buffer = blob(("dataforge.brave:" + os.path.normcase(str(root))).encode("utf-8"))
    result = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                         ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    try:
        if not function(ctypes.byref(source), None, ctypes.byref(entropy), None, None,
                        1, ctypes.byref(result)):
            raise ValueError("web_search_connection_invalid")
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        if result.pbData:
            kernel.LocalFree(ctypes.cast(result.pbData, ctypes.c_void_p))


def _read_descriptor(path: Path) -> int:
    if os.name != "nt":
        return os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    # O_NOFOLLOW is not available on Windows. Open the reparse point itself,
    # never its target, then verify the handle/path identity before reading.
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(path), 0x80000000, 7, None, 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ValueError("web_search_connection_invalid")
    try:
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except Exception:
        kernel.CloseHandle(handle)
        raise ValueError("web_search_connection_invalid") from None


def _read(path: Path, root: Path) -> _Connection | None:
    _unlinked(path)
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(before.st_mode) or before.st_size > MAX_CONNECTION_BYTES
            or (os.name != "nt" and stat.S_IMODE(before.st_mode) & 0o077)):
        raise ValueError("web_search_connection_invalid")
    fd = _read_descriptor(path)
    with os.fdopen(fd, "rb") as handle:
        opened = os.fstat(handle.fileno())
        _unlinked(path)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
            raise ValueError("web_search_connection_invalid")
        content = handle.read(MAX_CONNECTION_BYTES + 1)
    if len(content) > MAX_CONNECTION_BYTES:
        raise ValueError("web_search_connection_invalid")
    document = json.loads(content)
    credential_field = "protected_key" if os.name == "nt" else "api_key"
    if (type(document) is not dict or set(document) != {"version", "revision", credential_field}
            or type(document["version"]) is not int or document["version"] != 1
            or type(document["revision"]) is not str or not _REVISION.fullmatch(document["revision"])):
        raise ValueError("web_search_connection_invalid")
    value = document[credential_field]
    if os.name == "nt":
        value = _protect(base64.b64decode(value, validate=True), root, decrypt=True).decode("utf-8")
    return _Connection(_key(value), "local", document["revision"])


def resolve_connection(root: Path | None = None) -> _Connection:
    """Resolve on every operation, so saves and environment updates are live."""
    root_id = "environment"
    if root is not None:
        root = _root(root)
        _guard(root)
        root_id = os.path.normcase(str(root))
        try:
            local = _read(root / ".dataforge" / "connections" / "brave.json", root)
        except Exception:
            raise _InvalidConnection("local") from None
        _guard(root)
        if local is not None:
            return local
    try:
        key = _key(os.environ.get(KEY_ENV, ""), empty=True)
    except ValueError:
        raise _InvalidConnection("environment") from None
    if not key:
        with _ENV_LOCK:
            _ENV_REVISIONS.pop(root_id, None)
        return _Connection("", "none", "none")
    # Revision IDs are random, not hashes or encodings of a credential. Keep
    # only the current environment value per root, bounded in process memory.
    with _ENV_LOCK:
        current = _ENV_REVISIONS.pop(root_id, None)
        revision = current[1] if current and current[0] == key else uuid.uuid4().hex
        _ENV_REVISIONS[root_id] = (key, revision)
        while len(_ENV_REVISIONS) > 256:
            _ENV_REVISIONS.popitem(last=False)
    return _Connection(key, "environment", revision)


def connection_capabilities(root: Path | None = None) -> dict:
    try:
        return resolve_connection(root).capabilities()
    except _InvalidConnection as error:
        return {"brave_configured": False, "key_source": error.key_source,
                "connection_revision": "invalid", "connection_error": "web_search_connection_invalid"}


def save_connection(root: Path, api_key: str) -> dict:
    """Save a local override; an empty value removes it and restores env use."""
    root = _root(root)
    _guard(root)
    key = _key(api_key, empty=True)
    _unlinked(root / ".dataforge" / "connections" / "brave.json")
    boundary = nullcontext()
    if getattr(workspace, "_require_shared_workspace_access", None) is not None:
        from lib import review_center

        reserve = getattr(review_center, "shared_cache_publish_boundary", None)
        if reserve is None:
            raise PermissionError("Cannot reserve team mode boundary")
        project_db = root / "data" / "review_center.db"
        _unlinked(project_db)
        boundary = reserve(project_db)
    # Reuse the private distribution's team-activation reservation rather than
    # inventing another tenant gate. Public installations need no team store.
    with boundary:
        _guard(root)
        return _save_local(root, key)


def _save_local(root: Path, key: str) -> dict:
    directory = root / ".dataforge" / "connections"
    target = directory / "brave.json"
    lock_path = directory / ".brave.lock"
    temporary = None
    try:
        _unlinked(directory)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not directory.is_dir():
            raise ValueError("web_search_connection_invalid")
        if os.name != "nt":
            os.chmod(directory, 0o700)
        _unlinked(lock_path)
        try:
            lock_info = lock_path.lstat()
        except FileNotFoundError:
            pass
        else:
            if not stat.S_ISREG(lock_info.st_mode):
                raise ValueError("web_search_connection_invalid")
        with FileLock(str(lock_path), timeout=3):
            _guard(root)
            _unlinked(target)
            if target.exists() and not target.is_file():
                raise ValueError("web_search_connection_invalid")
            if not key:
                target.unlink(missing_ok=True)
                return connection_capabilities(root)
            credential_field = "protected_key" if os.name == "nt" else "api_key"
            value = (base64.b64encode(_protect(key.encode("utf-8"), root)).decode("ascii")
                     if os.name == "nt" else key)
            document = {"version": 1, "revision": uuid.uuid4().hex, credential_field: value}
            content = json.dumps(document, separators=(",", ":")).encode("utf-8")
            if len(content) > MAX_CONNECTION_BYTES:
                raise ValueError("web_search_connection_invalid")
            fd, temporary = tempfile.mkstemp(prefix=".brave-", suffix=".tmp", dir=directory)
            with os.fdopen(fd, "wb") as handle:
                os.chmod(temporary, 0o600)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            _guard(root)
            _unlinked(directory)
            _unlinked(target)
            _replace_state(temporary, target)
            temporary = None
            return {"brave_configured": True, "key_source": "local",
                    "connection_revision": document["revision"]}
    except PermissionError:
        raise  # Keep the existing private team access boundary authoritative.
    except Exception:
        raise ValueError("web_search_connection_invalid") from None
    finally:
        if temporary is not None:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass
