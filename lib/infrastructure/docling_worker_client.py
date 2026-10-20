"""A bounded, cancellable process boundary for an explicit local Docling runtime.

The worker protocol carries local source/model paths, never API credentials.
Third-party logs stay in an automatically removed temporary directory and are
not included in user-facing errors. This module does not import Docling.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


_MAX_RESPONSE_BYTES = 64 * 1024 * 1024
_POLL_SECONDS = 0.1
_ENTRYPOINT = Path(__file__).with_name("docling_worker.py")
_CLEANUP_DELAYS = (0, .05, .1, .2, .4, .8, 1)


def _cleanup_owned_directory(directory, owned_path):
    """Retry transient Windows sharing violations, only for this minted path."""
    for delay in _CLEANUP_DELAYS:
        if delay:
            time.sleep(delay)
        try:
            # Resolve before every recursive cleanup. A worker cannot redirect
            # cleanup through a replaced root/junction into another directory.
            current = Path(directory.name)
            if current.absolute() != owned_path or current.resolve() != owned_path:
                raise ValueError("document_docling_worker_cleanup_failed")
            directory.cleanup()
            if not current.exists():
                return
        except OSError:
            # Process completion and closing the parent's streams may precede
            # the release of inherited/scanner handles on Windows slightly.
            continue
    raise ValueError("document_docling_worker_cleanup_failed")


@contextmanager
def _owned_temporary_directory():
    try:
        directory = tempfile.TemporaryDirectory(prefix="dataforge-docling-", ignore_cleanup_errors=False)
        owned_path = Path(directory.name).resolve()
    except OSError:
        raise ValueError("document_docling_worker_io_failed") from None
    try:
        yield owned_path
    finally:
        original = sys.exc_info()[1]
        try:
            _cleanup_owned_directory(directory, owned_path)
        except ValueError:
            if original is None:
                raise ValueError("document_docling_worker_cleanup_failed") from None
            # Preserve cancellation identity/type/traceback even if a handle is
            # held beyond the finite retry window. Do not silently hide failure
            # or include private filenames or third-party log content.
            original.add_note("document_docling_worker_cleanup_failed")


class _WorkerProcessTree:
    """Track only this worker's descendants, including Windows venv launchers.

    Keep creation times so an exited PID reused by another program can never
    become a cancellation target. Remember children while the launcher lives:
    their parent relationship may disappear when that launcher exits.
    """

    def __init__(self, process, psutil):
        self.process = process
        self.psutil = psutil
        self.known = {}
        try:
            self._remember(psutil.Process(process.pid))
        except psutil.NoSuchProcess:
            pass

    def _remember(self, process):
        try:
            self.known[(process.pid, process.create_time())] = process
        except self.psutil.NoSuchProcess:
            pass

    def _live(self):
        live = []
        for (pid, created), remembered in tuple(self.known.items()):
            try:
                current = self.psutil.Process(pid)
                if (current.create_time() == created and remembered.is_running()
                        and current.status() != self.psutil.STATUS_ZOMBIE):
                    live.append(remembered)
            except self.psutil.NoSuchProcess:
                continue
        return live

    def refresh(self):
        for parent in self._live():
            try:
                for child in parent.children(recursive=True):
                    self._remember(child)
            except self.psutil.NoSuchProcess:
                continue

    def stop(self):
        self.refresh()
        # Stop descendants before the launcher. Killing just python.exe from a
        # Windows venv leaves its actual Python interpreter holding log files.
        processes = list(reversed(self._live()))
        for process in processes:
            try:
                process.terminate()
            except self.psutil.NoSuchProcess:
                pass
        _, remaining = self.psutil.wait_procs(processes, timeout=2)
        for process in remaining:
            try:
                process.kill()  # psutil checks PID reuse before signalling.
            except self.psutil.NoSuchProcess:
                pass
        _, remaining = self.psutil.wait_procs(remaining, timeout=5)
        # Reap our Popen as well, closing its platform process bookkeeping.
        self.process.wait(timeout=1)
        if remaining:
            raise ValueError("document_docling_worker_stop_failed")


def _read_response(path):
    try:
        with path.open("rb") as stream:
            if os.fstat(stream.fileno()).st_size > _MAX_RESPONSE_BYTES:
                raise ValueError("document_docling_worker_response_too_large")
            # Also bound the actual read if the file changes after fstat.
            data = stream.read(_MAX_RESPONSE_BYTES + 1)
    except FileNotFoundError:
        raise ValueError("document_docling_worker_response_missing") from None
    except OSError:
        raise ValueError("document_docling_worker_response_invalid") from None
    if len(data) > _MAX_RESPONSE_BYTES:
        raise ValueError("document_docling_worker_response_too_large")
    try:
        def reject_constant(value):
            raise ValueError("nonfinite_json_constant")
        result = json.loads(data.decode("utf-8"), parse_constant=reject_constant)
    except (UnicodeError, ValueError, RecursionError):
        raise ValueError("document_docling_worker_response_invalid") from None
    if not isinstance(result, dict):
        raise ValueError("document_docling_worker_response_invalid")
    return result


def run_docling_worker(python_path, request: dict, *, check_cancel=None,
                       timeout_seconds=1800, _entrypoint=None) -> dict:
    """Run one local worker and return its validated JSON object.

``_entrypoint`` exists only for small real-process contract tests. Production
callers use the fixed sibling script. The script must accept ``--request`` and
``--response`` paths and add the repository root to sys.path itself: Python's
isolated flag intentionally ignores an inherited PYTHONPATH/user site.
"""
    if (not isinstance(request, dict) or isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise ValueError("document_docling_worker_invalid_request")
    try:
        encoded_request = json.dumps(request, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise ValueError("document_docling_worker_invalid_request") from None
    try:
        python = Path(python_path)
        if not python.is_absolute() or not python.is_file():
            raise ValueError("document_docling_worker_runtime_unavailable")
        entrypoint = Path(_entrypoint) if _entrypoint is not None else _ENTRYPOINT
        if not entrypoint.is_absolute() or not entrypoint.is_file():
            raise ValueError("document_docling_worker_entrypoint_missing")
    except (TypeError, OSError):
        raise ValueError("document_docling_worker_runtime_unavailable") from None
    if check_cancel:
        check_cancel()
    try:
        import psutil
    except ImportError:
        # Fail before creating a worker we cannot reliably stop.
        raise ValueError("document_docling_worker_runtime_unavailable") from None
    environment = dict(os.environ)
    environment.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                       HF_HUB_DISABLE_TELEMETRY="1", DO_NOT_TRACK="1",
                       TOKENIZERS_PARALLELISM="false")
    process = None
    # No caller-supplied deletion target. Wait for transient handle release
    # after reaping the worker and closing our streams, before returning.
    with _owned_temporary_directory() as root:
        input_path, output_path = root / "request.json", root / "response.json"
        with ExitStack() as files:
            try:
                input_path.write_bytes(encoded_request)
                stdout = files.enter_context((root / "stdout.log").open("wb"))
                stderr = files.enter_context((root / "stderr.log").open("wb"))
            except OSError:
                raise ValueError("document_docling_worker_io_failed") from None
            try:
                process = subprocess.Popen(
                    [str(python), "-I", "-X", "utf8", str(entrypoint), "--request", str(input_path),
                     "--response", str(output_path)],
                    stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                    shell=False, close_fds=True, env=environment,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError:
                raise ValueError("document_docling_worker_start_failed") from None
            tree = _WorkerProcessTree(process, psutil)
            deadline = time.monotonic() + timeout_seconds
            try:
                while True:
                    tree.refresh()
                    if check_cancel:
                        check_cancel()
                    code = process.poll()
                    if code is not None:
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ValueError("document_docling_worker_timeout")
                    time.sleep(min(_POLL_SECONDS, remaining))
            finally:
                original = sys.exc_info()[1]
                try:
                    tree.stop()
                except (OSError, subprocess.TimeoutExpired, psutil.Error, ValueError):
                    if original is None:
                        raise ValueError("document_docling_worker_stop_failed") from None
                    # Preserve cancellation/timeout even if a process cannot be
                    # stopped, but make the bounded cleanup failure explicit.
                    original.add_note("document_docling_worker_stop_failed")
        if code != 0:
            raise ValueError("document_docling_worker_failed")
        if check_cancel:
            check_cancel()
        return _read_response(output_path)
