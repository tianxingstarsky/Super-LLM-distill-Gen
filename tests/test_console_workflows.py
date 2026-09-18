"""Exercise the actual Streamlit form and job runner without paid model calls."""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
import pytest

ROOT = Path(__file__).resolve().parent.parent
def test_cli_default_workspace_override_does_not_leak(tmp_path, monkeypatch, capsys):
    from lib import cli, workspace as ws
    monkeypatch.setattr(ws, "ROOT", tmp_path)
    monkeypatch.setattr(ws, "WORKSPACES_DIR", tmp_path / "data/workspaces")
    monkeypatch.setattr(ws, "CURRENT_PATH", tmp_path / "current.json")
    monkeypatch.setattr(ws, "REGISTRY_PATH", tmp_path / "data/workspaces.json")
    monkeypatch.setattr(ws, "SEEDS_DIR", tmp_path / "data/seeds")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setenv("DF_WORKSPACE", "alpha")
    monkeypatch.setattr("sys.argv", ["df", "workspace", "status", "--ws", "default"])
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["out_dir"] == str(tmp_path / "data/output")
    assert os.environ['DF_WORKSPACE'] == 'alpha'
def test_missing_corpus_input_preserves_existing_output(tmp_path, monkeypatch):
    from lib import cli
    from types import SimpleNamespace
    existing = tmp_path / "corpus.jsonl"
    existing.write_text('{"text":"preserve me"}\n', encoding="utf-8")
    with pytest.raises(ValueError):
        cli.cmd_doc2corpus(SimpleNamespace(input=str(tmp_path / "missing"), out=str(existing), chunk_size=None, overlap=None))
    assert existing.read_text(encoding="utf-8") == '{"text":"preserve me"}\n'


def test_job_lock_serializes_same_workspace(tmp_path):
    """工作区级文件锁：任务只在浏览器会话登记，跨窗口不得并发写同一输出目录。"""
    from filelock import Timeout
    from lib.console_jobs import Job

    root = tmp_path / "app"
    job1 = Job(["__no_such_command__"], "default", root)
    job1.start()
    job2 = Job(["__no_such_command__"], "default", root)
    with pytest.raises(Timeout):
        job2.start()  # 同工作区第二个任务被拒

    deadline = time.monotonic() + 30
    while job1.snapshot()[0] is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert job1.code is not None
    # filelock 在释放时删除锁文件；锁的持有已由上方 Timeout 验证

    job3 = Job(["__no_such_command__"], "default", root)
    job3.start()  # 锁已随任务结束释放
    while job3.snapshot()[0] is None and time.monotonic() < deadline:
        time.sleep(0.05)
    assert job3.code is not None
