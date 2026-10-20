"""Exercise real short Python workers, including cancellation and noisy logs."""
import json
import os
from pathlib import Path
import sys
import threading
import time

import pytest

from lib.infrastructure import docling_worker_client as client


@pytest.fixture
def worker(tmp_path):
    # Spaces and non-ASCII characters verify argv/file handling without a shell.
    path = tmp_path / "解析 worker.py"
    path.write_text('''
import argparse, json, os, pathlib, signal, subprocess, sys, time
p=argparse.ArgumentParser();p.add_argument('--request');p.add_argument('--response');a=p.parse_args()
request_path=pathlib.Path(a.request);response_path=pathlib.Path(a.response)
r=json.loads(request_path.read_text(encoding='utf-8'))
mode=r.get('mode','echo')
state={'pid':os.getpid(),'temporary':str(request_path.parent)}
state.update(r.get('nested_state',{}))
if mode=='nested':
    sys.path.insert(0,r['repo'])
    from lib.infrastructure.docling_worker_client import _owned_temporary_directory, run_docling_worker
    with _owned_temporary_directory() as crops:
        (crops/'private-crop.png').write_bytes(b'fixture pixels')
        run_docling_worker(sys.executable,{
            'mode':'sleep','marker':r['marker'],
            'nested_state':{'outer_pid':os.getpid(),'outer_temporary':str(request_path.parent),
                            'crop_temporary':str(crops)}},
            _entrypoint=pathlib.Path(__file__).resolve(),timeout_seconds=20)
if mode=='tree':
    child_marker=request_path.parent/'child.json'
    child_script='import json,os,pathlib,sys,time;pathlib.Path(sys.argv[1]).write_text(json.dumps({"pid":os.getpid()}));time.sleep(60)'
    child=subprocess.Popen([sys.executable,'-c',child_script,str(child_marker)],
        stdin=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    while not child_marker.exists() or not child_marker.stat().st_size:time.sleep(.01)
    state['child_launcher_pid']=child.pid
    state['child_pid']=json.loads(child_marker.read_text())['pid']
if r.get('marker'):
    marker=pathlib.Path(r['marker']);pending=marker.with_suffix('.pending')
    pending.write_text(json.dumps(state),encoding='utf-8');pending.replace(marker)
if mode=='fail':
    print('private-fixture-key-do-not-expose',file=sys.stderr);sys.exit(17)
if mode in {'sleep','tree'}:
    if r.get('ignore_terminate') and os.name!='nt':signal.signal(signal.SIGTERM,signal.SIG_IGN)
    while True: time.sleep(.1)
if mode=='missing':sys.exit(0)
if mode=='oversize':
    with response_path.open('wb') as f:f.truncate(64*1024*1024+1)
elif mode=='badjson':response_path.write_text('{private-fixture-key-do-not-expose',encoding='utf-8')
elif mode=='utf8':response_path.write_bytes(b'\\xff')
elif mode=='array':response_path.write_text('[]',encoding='utf-8')
elif mode=='nan':response_path.write_text('{"value":NaN}',encoding='utf-8')
elif mode=='deep':response_path.write_text('{"x":'+('['*2000)+(']'*2000)+'}',encoding='utf-8')
else:
    if r.get('noisy'):
        sys.stdout.write('x'*200000);sys.stderr.write('y'*200000)
    response_path.write_text(json.dumps({'request':r,'temporary':str(request_path.parent),
       'isolated':sys.flags.isolated,'utf8_mode':sys.flags.utf8_mode,'offline':os.environ.get('HF_HUB_OFFLINE'),
       'docling_imported':'docling' in sys.modules},ensure_ascii=False),encoding='utf-8')
''', encoding="utf-8")
    return path


def run(worker, request, **kwargs):
    return client.run_docling_worker(sys.executable, request, _entrypoint=worker, **kwargs)


def test_real_worker_preserves_unicode_metadata_and_does_not_block_on_logs(worker):
    request = {"source_path": "F:/资料/论文 with spaces.pdf", "config": {"ocr": False},
               "source": {"page": 5, "charspan": [12, 30]}, "noisy": True}
    result = run(worker, request, timeout_seconds=10)
    assert result["request"] == request
    assert result["isolated"] == 1
    assert result["utf8_mode"] == 1
    assert result["offline"] == "1"
    assert result["docling_imported"] is False
    assert not Path(result["temporary"]).exists()


@pytest.mark.parametrize("mode,error", [
    ("fail", "document_docling_worker_failed"),
    ("missing", "document_docling_worker_response_missing"),
    ("oversize", "document_docling_worker_response_too_large"),
    ("badjson", "document_docling_worker_response_invalid"),
    ("utf8", "document_docling_worker_response_invalid"),
    ("array", "document_docling_worker_response_invalid"),
    ("nan", "document_docling_worker_response_invalid"),
    ("deep", "document_docling_worker_response_invalid"),
])
def test_worker_failure_is_controlled_and_cleans_files(worker, tmp_path, mode, error):
    marker = tmp_path / "worker-state.json"
    with pytest.raises(ValueError) as raised:
        run(worker, {"mode": mode, "marker": str(marker)}, timeout_seconds=10)
    assert str(raised.value) == error
    assert "private-fixture-key" not in str(raised.value)
    assert not Path(json.loads(marker.read_text())["temporary"]).exists()


def alive(pid):
    if os.name == "nt":
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
            return code.value == 259
        finally:
            kernel.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


@pytest.mark.parametrize("error_type", [RuntimeError, OSError])
def test_cancellation_preserves_original_exception_and_reaps_process(worker, tmp_path, error_type):
    marker = tmp_path / "cancel-state.json"
    original = error_type("original cancellation object")
    def cancel():
        if marker.exists():
            raise original
    with pytest.raises(error_type) as raised:
        run(worker, {"mode": "sleep", "marker": str(marker)}, check_cancel=cancel, timeout_seconds=10)
    assert raised.value is original
    state = json.loads(marker.read_text())
    assert not alive(state["pid"])
    assert not Path(state["temporary"]).exists()


@pytest.mark.parametrize("reason", ["cancel", "timeout"])
def test_worker_descendants_are_reaped_including_venv_launchers(worker, tmp_path, reason):
    marker = tmp_path / "tree-state.json"
    original = OSError("original tree cancellation")
    def cancel():
        if reason == "cancel" and marker.exists():
            raise original
    # On Windows explicitly go through the project's venv redirector. The
    # worker itself launches another interpreter which inherits its log files.
    venv = Path(__file__).resolve().parents[1] / ".venv/Scripts/python.exe"
    python = str(venv) if os.name == "nt" and venv.is_file() else sys.executable
    with pytest.raises(OSError if reason == "cancel" else ValueError) as raised:
        client.run_docling_worker(python, {"mode": "tree", "marker": str(marker)},
                                  check_cancel=cancel, timeout_seconds=1.5, _entrypoint=worker)
    if reason == "cancel":
        assert raised.value is original
        assert not getattr(original, "__notes__", [])
    else:
        assert str(raised.value) == "document_docling_worker_timeout"
    state = json.loads(marker.read_text())
    assert not alive(state["pid"])
    assert not alive(state["child_launcher_pid"])
    assert not alive(state["child_pid"])
    assert not Path(state["temporary"]).exists()


def test_cancel_cleans_nested_worker_ipc_and_crop_directories(worker, tmp_path):
    marker = tmp_path / "nested-state.json"
    original = OSError("original nested cancellation")

    def cancel():
        if marker.exists():
            raise original

    with pytest.raises(OSError) as raised:
        run(worker, {"mode": "nested", "marker": str(marker),
                     "repo": str(Path(__file__).resolve().parents[1])},
            check_cancel=cancel, timeout_seconds=10)
    assert raised.value is original
    assert not getattr(original, "__notes__", [])
    state = json.loads(marker.read_text())
    outer = Path(state["outer_temporary"])
    nested = Path(state["temporary"])
    crops = Path(state["crop_temporary"])
    assert nested.parent == outer
    assert crops.parent == outer
    assert not alive(state["outer_pid"])
    assert not alive(state["pid"])
    assert not outer.exists()
    assert not nested.exists()
    assert not crops.exists()


@pytest.mark.skipif(os.name != "nt", reason="Exercise an actual Windows file-sharing violation")
def test_cancel_retries_locked_log_cleanup_without_losing_original_error(worker, tmp_path, monkeypatch):
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    marker = tmp_path / "locked-cancel-state.json"
    original = OSError("same cancellation object")
    handles, timers, attempts = [], [], []
    real_cleanup = client.tempfile.TemporaryDirectory.cleanup
    def observed_cleanup(directory):
        attempts.append(directory.name)
        if not timers:
            # Start the release clock at cleanup, not cancellation: stopping a
            # real Windows process tree can itself take longer than 0.4 s.
            def release():
                kernel.CloseHandle(handles[0])
                handles.clear()
            timer = threading.Timer(.4, release)
            timers.append(timer)
            timer.start()
        return real_cleanup(directory)
    monkeypatch.setattr(client.tempfile.TemporaryDirectory, "cleanup", observed_cleanup)
    def cancel():
        if not marker.exists():
            return
        state = json.loads(marker.read_text())
        log = str(Path(state["temporary"]) / "stderr.log")
        # Read/write sharing permits the worker's open stream; withholding
        # FILE_SHARE_DELETE reproduces the Windows cleanup race deterministically.
        handle = kernel.CreateFileW(log, 0x80000000, 3, None, 3, 0x80, None)
        assert handle != ctypes.c_void_p(-1).value
        handles.append(handle)
        raise original
    try:
        with pytest.raises(OSError) as raised:
            run(worker, {"mode": "sleep", "marker": str(marker)}, check_cancel=cancel, timeout_seconds=10)
        assert raised.value is original
        assert len(attempts) >= 2
        state = json.loads(marker.read_text())
        assert not alive(state["pid"])
        assert not Path(state["temporary"]).exists()
        assert not getattr(original, "__notes__", [])
    finally:
        for timer in timers:
            timer.join(timeout=2)
        for handle in handles:
            kernel.CloseHandle(handle)


def test_exhausted_cleanup_reports_failure_and_preserves_cancellation(tmp_path, monkeypatch):
    original = OSError("original cancellation")
    created = []
    real_cleanup = client.tempfile.TemporaryDirectory.cleanup
    def locked(directory):
        created.append(directory)
        raise PermissionError("private-path-not-for-display")
    monkeypatch.setattr(client.tempfile.TemporaryDirectory, "cleanup", locked)
    monkeypatch.setattr(client, "_CLEANUP_DELAYS", (0, 0, 0))
    try:
        with pytest.raises(OSError) as raised:
            with client._owned_temporary_directory():
                raise original
        assert raised.value is original
        assert original.__notes__ == ["document_docling_worker_cleanup_failed"]
        assert len(created) == 3
        with pytest.raises(ValueError, match="^document_docling_worker_cleanup_failed$"):
            with client._owned_temporary_directory():
                pass
        assert len(created) == 6
    finally:
        for directory in created:
            real_cleanup(directory)


def test_timeout_reaps_process_and_cleans_files(worker, tmp_path):
    marker = tmp_path / "timeout-state.json"
    started = time.monotonic()
    with pytest.raises(ValueError, match="^document_docling_worker_timeout$"):
        run(worker, {"mode": "sleep", "marker": str(marker)}, timeout_seconds=.7)
    assert time.monotonic() - started < 5
    state = json.loads(marker.read_text())
    assert not alive(state["pid"])
    assert not Path(state["temporary"]).exists()


@pytest.mark.skipif(os.name == "nt", reason="Windows terminate already forcefully terminates the worker")
def test_worker_ignoring_terminate_is_killed(worker, tmp_path):
    marker = tmp_path / "kill-state.json"
    with pytest.raises(ValueError, match="^document_docling_worker_timeout$"):
        run(worker, {"mode": "sleep", "marker": str(marker), "ignore_terminate": True}, timeout_seconds=.7)
    state = json.loads(marker.read_text())
    assert not alive(state["pid"])
    assert not Path(state["temporary"]).exists()


def test_pre_cancel_does_not_start_worker(worker, tmp_path):
    marker = tmp_path / "not-started.json"
    original = RuntimeError("stop")
    def cancel():
        raise original
    with pytest.raises(RuntimeError) as raised:
        run(worker, {"marker": str(marker)}, check_cancel=cancel)
    assert raised.value is original
    assert not marker.exists()


@pytest.mark.parametrize("value", [0, -1, True, float("nan"), float("inf"), "30"])
def test_invalid_timeout_does_not_start(worker, value):
    with pytest.raises(ValueError, match="^document_docling_worker_invalid_request$"):
        run(worker, {}, timeout_seconds=value)


@pytest.mark.parametrize("payload", [[], {"bad": object()}, {"bad": float("nan")}])
def test_request_must_be_json_object(worker, payload):
    with pytest.raises(ValueError, match="^document_docling_worker_invalid_request$"):
        run(worker, payload)


def test_missing_runtime_and_fixed_entrypoint_errors(worker, tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="^document_docling_worker_runtime_unavailable$"):
        client.run_docling_worker(tmp_path / "missing-python", {}, _entrypoint=worker)
    monkeypatch.setattr(client, "_ENTRYPOINT", tmp_path / "missing-worker.py")
    with pytest.raises(ValueError, match="^document_docling_worker_entrypoint_missing$"):
        client.run_docling_worker(sys.executable, {})
