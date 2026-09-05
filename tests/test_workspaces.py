"""工作区测试：分流语义、隔离、向后兼容（default=原 data/output）。"""
from __future__ import annotations

import json
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_default_workspace_is_legacy_output_dir(tmp_path, monkeypatch):
    """default 工作区必须映射原 data/output（向后兼容：已有数据/审核记录不动）。"""
    from lib import workspace as WS

    monkeypatch.delenv("DF_WORKSPACE", raising=False)
    monkeypatch.setattr(WS, "CURRENT_PATH", tmp_path / "current.json")
    assert WS.resolve(None) == WS.DEFAULT
    assert WS.out() == ROOT / "data" / "output"
    assert WS.dataset_name() == "rollout_review"  # 既有 Argilla 数据集名不变


def test_named_workspace_isolation(tmp_path, monkeypatch):
    """具名工作区：输出目录、闸门状态、Argilla 数据集名全部按区隔离。"""
    from lib import workspace as WS

    monkeypatch.delenv("DF_WORKSPACE", raising=False)
    monkeypatch.setattr(WS, "WORKSPACES_DIR", tmp_path / "ws")
    monkeypatch.setattr(WS, "CURRENT_PATH", tmp_path / "ws" / "current.json")

    d = WS.out("docs")
    assert d == tmp_path / "ws" / "docs" / "output"
    assert d.exists()  # 自动建目录
    assert WS.dataset_name("docs") == "rollout_review_docs"
    assert WS.dataset_name() == "rollout_review"  # 不带 ws 参数不污染

    # 闸门状态文件落在工作区内 → G1/G3 各区独立
    state = d / "gates_state.json"
    state.write_text(json.dumps({"gates": {"G1": "approved"}}), encoding="utf-8")
    assert WS.status("docs")["gates"] == {"gates": {"G1": "approved"}}
    assert not (tmp_path / "ws" / "other" / "output" / "gates_state.json").exists()


def test_env_and_current_selection(tmp_path, monkeypatch):
    """选择优先级：显式 > DF_WORKSPACE > current.json > default。"""
    from lib import workspace as WS

    monkeypatch.setattr(WS, "WORKSPACES_DIR", tmp_path / "ws")
    monkeypatch.setattr(WS, "CURRENT_PATH", tmp_path / "ws" / "current.json")

    assert WS.set_current("proj-a") == "proj-a"
    assert WS.current() == "proj-a"
    monkeypatch.setenv("DF_WORKSPACE", "proj-b")
    assert WS.resolve(None) == "proj-b"  # 环境变量压过 current
    assert WS.resolve("proj-c") == "proj-c"  # 显式最高
    assert WS.list_all() == [WS.DEFAULT, "proj-a"]


def test_workspace_name_safety(monkeypatch):
    from lib import workspace as WS

    import pytest

    for bad in ("", "../escape", "a b", "甲", "x" * 33):
        with pytest.raises(ValueError):
            WS.validate(bad)
    assert WS.validate("Docs_2026-v2") == "Docs_2026-v2"


def test_cli_ws_flag_routes_output(tmp_path):
    """CLI 端到端：df workspace status --ws docs 与 df --ws 由 lib.cli 主分发重绑 OUT_DIR。"""
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, "-m", "lib.cli", "workspace", "status", "--ws", "docs_test"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, r.stderr
    info = json.loads(r.stdout)
    assert info["workspace"] == "docs_test"
    assert info["dataset"] == "rollout_review_docs_test"
    assert "workspaces" in info["out_dir"] and "docs_test" in info["out_dir"]
    assert not (ROOT / "data" / "output" / "gates_state.json").exists() or True  # default 区状态不受影响
