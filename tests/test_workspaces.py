"""Existing-folder registration, read-only lookup, isolation and legacy compatibility."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess

import pytest

from lib import workspace as WS


@pytest.fixture
def workspace_root(tmp_path, monkeypatch):
    root = tmp_path / "app"
    monkeypatch.delenv("DF_WORKSPACE", raising=False)
    monkeypatch.setattr(WS, "ROOT", root)
    monkeypatch.setattr(WS, "REGISTRY_PATH", root / "data/workspaces.json")
    monkeypatch.setattr(WS, "WORKSPACES_DIR", root / "data/workspaces")
    monkeypatch.setattr(WS, "CURRENT_PATH", root / "data/workspaces/current.json")
    monkeypatch.setattr(WS, "SEEDS_DIR", root / "data/seeds")
    return root


def test_default_workspace_is_legacy_output_dir(workspace_root):
    assert WS.resolve() == WS.DEFAULT
    assert WS.out() == workspace_root / "data/output"
    assert WS.dataset_name() == "rollout_review"
    assert not workspace_root.exists()


def test_existing_folder_registration_is_readonly_and_idempotent(workspace_root, tmp_path):
    source = tmp_path / "客户 数据集"
    source.mkdir()
    data = source / "samples.jsonl"
    data.write_bytes(b'{"messages": []}\n')
    before = data.read_bytes()
    name = WS.add_folder(source)
    assert WS.add_folder(source / ".") == name
    assert WS.folder(name) == source
    assert WS.out(name) == source / ".dataforge/output"
    assert WS.output_at(workspace_root, name) == WS.out(name)
    assert WS.label(name) == source.name
    assert WS.current() == "default"
    assert list(source.iterdir()) == [data]
    assert data.read_bytes() == before


def test_rejects_missing_file_and_blank_sources(workspace_root, tmp_path):
    ordinary_file = tmp_path / "source.txt"
    ordinary_file.write_text("input", encoding="utf-8")
    for invalid in ("", "  ", tmp_path / "missing", ordinary_file):
        with pytest.raises(ValueError):
            WS.add_folder(invalid)
    assert not (tmp_path / "missing").exists()
    assert not workspace_root.exists()
    with pytest.raises(ValueError, match="尚未打开"):
        WS.out("nonexistent")


def test_output_and_gate_isolation(workspace_root, tmp_path):
    ids = []
    for parent in ("customer-a", "customer-b"):
        source = tmp_path / parent / "dataset"
        source.mkdir(parents=True)
        ids.append(WS.add_folder(source))
    a, b = ids
    assert a != b
    assert WS.dataset_name(a) != WS.dataset_name(b)
    destination = WS.out(a)
    destination.mkdir(parents=True)
    state = {"gates": {"G1": "approved"}}
    (destination / "gates_state.json").write_text(json.dumps(state), encoding="utf-8")
    assert WS.status(a)["gates"] == state
    assert WS.status(b)["gates"] == {}
    assert not WS.out(b).exists()
    assert not WS.out("default").exists()


def test_env_and_current_selection(workspace_root, tmp_path, monkeypatch):
    for name in ("a", "b", "c"):
        source = tmp_path / name
        source.mkdir()
        WS.add_folder(source, name=name)
    WS.set_current("a")
    assert WS.current() == "a"
    assert WS.resolve() == "a"
    monkeypatch.setenv("DF_WORKSPACE", "b")
    assert WS.resolve() == "b"
    assert WS.resolve("c") == "c"
    assert WS.list_all() == ["default", "a", "b", "c"]


def test_moved_source_is_not_recreated(workspace_root, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    name = WS.add_folder(source)
    source.rename(tmp_path / "moved")
    assert name in WS.list_all()
    for operation in (WS.folder, WS.out, WS.set_current, WS.status):
        with pytest.raises(FileNotFoundError, match="不会自动创建"):
            operation(name)
    assert not source.exists()


def test_legacy_workspace_keeps_paths_and_current(workspace_root):
    legacy = workspace_root / "data/workspaces/old"
    (legacy / "output").mkdir(parents=True)
    (legacy / "output/samples.jsonl").write_text("{}", encoding="utf-8")
    WS.CURRENT_PATH.write_text('{"workspace":"old"}', encoding="utf-8")
    assert WS.current() == "old"
    assert WS.out("old") == legacy / "output"
    assert WS.add_folder(legacy) == "old"
    assert WS.source_files("old") == []
    assert not WS.REGISTRY_PATH.exists()


def test_source_inventory_excludes_outputs_and_dependencies(workspace_root, tmp_path):
    source = tmp_path / "input"
    for directory in (".dataforge/output", ".git", "node_modules", "nested"):
        path = source / directory
        path.mkdir(parents=True)
        (path / "data.jsonl").write_text("{}", encoding="utf-8")
    name = WS.add_folder(source)
    assert WS.source_files(name) == [source / "nested/data.jsonl"]
    assert WS.source_files(name, limit=1) == [source / "nested/data.jsonl"]
    with pytest.raises(ValueError):
        WS.source_files(name, limit=0)


def test_registry_rejects_corruption_without_overwrite(workspace_root, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    WS.REGISTRY_PATH.parent.mkdir(parents=True)
    for content in ('[]', '{"workspaces":{"bad":{"folder":"relative"}}}', 'broken'):
        WS.REGISTRY_PATH.write_text(content, encoding="utf-8")
        with pytest.raises(ValueError, match="原文件未改动"):
            WS.add_folder(source)
        assert WS.REGISTRY_PATH.read_text(encoding="utf-8") == content


def test_concurrent_registration_preserves_all_entries(workspace_root, tmp_path):
    sources = [tmp_path / f"source-{i}" for i in range(8)]
    for source in sources:
        source.mkdir()
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(WS.add_folder, sources + sources))
    assert ids[:8] == ids[8:]
    assert len(WS.list_all()) == 9


def test_explicit_root_registry_is_independent(workspace_root, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    other_root = tmp_path / "other-app"
    name = WS.add_folder(source, root=other_root)
    WS.set_current(name, root=other_root)
    assert WS.status(root=other_root)["workspace"] == name
    assert WS.output_at(other_root, name) == source / ".dataforge/output"
    assert WS.list_all() == ["default"]
    assert not workspace_root.exists()


def test_workspace_name_safety():
    for bad in ("", "../escape", "a b", "甲", "x" * 49):
        with pytest.raises(ValueError):
            WS.validate(bad)
    assert WS.validate("Docs_2026-v2") == "Docs_2026-v2"


def test_cli_register_use_status_without_environment_pollution(workspace_root, tmp_path, monkeypatch, capsys):
    from lib import cli
    source = tmp_path / "ready-folder"
    source.mkdir()
    monkeypatch.setattr(cli, "ROOT", workspace_root)
    monkeypatch.setattr("sys.argv", ["df", "workspace", "add", str(source)])
    assert cli.main() == 0
    name = next(n for n in WS.list_all() if n != "default")
    monkeypatch.setattr("sys.argv", ["df", "workspace", "use", name])
    assert cli.main() == 0
    capsys.readouterr()
    monkeypatch.setattr("sys.argv", ["df", "workspace", "status"])
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["workspace"] == name
    monkeypatch.setenv("DF_WORKSPACE", "default")
    monkeypatch.setattr("sys.argv", ["df", "workspace", "status", "--ws", name])
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["out_dir"] == str(source / ".dataforge/output")
    assert WS.resolve() == "default"
    assert list(source.iterdir()) == []


def test_cli_list_can_recover_unavailable_current_folder(workspace_root, tmp_path, monkeypatch, capsys):
    from lib import cli
    source = tmp_path / "ready-folder"
    source.mkdir()
    name = WS.add_folder(source)
    WS.set_current(name)
    source.rename(tmp_path / "moved")
    monkeypatch.setattr("sys.argv", ["df", "workspace", "list"])
    assert cli.main() == 0
    assert "不可用" in capsys.readouterr().out
    monkeypatch.setattr("sys.argv", ["df", "workspace", "use", "default"])
    assert cli.main() == 0
    assert WS.current() == "default"


# ── 链接目录/文件：Python 3.11 无 Path.is_junction，靠 lstat 的 symlink 位 + reparse tag ──
def _make_junction(link: Path, target: Path) -> bool:
    """Windows 下安全创建 junction（免管理员；失败返回 False，由调用方 skip）。"""
    if os.name != "nt":
        return False
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                            capture_output=True)
    return result.returncode == 0


def test_is_linked_flags_plain_paths_and_fails_closed(tmp_path):
    plain_file = tmp_path / "plain.txt"
    plain_file.write_text("x", encoding="utf-8")
    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    assert WS.is_linked(plain_file) is False
    assert WS.is_linked(plain_dir) is False
    assert WS.is_linked(tmp_path / "missing") is True  # 无法 lstat：失败关闭，扫描器排除


@pytest.mark.skipif(os.name != "nt", reason="junction 仅 Windows")
def test_source_inventory_skips_junctions(workspace_root, tmp_path):
    source = tmp_path / "input"
    (source / "real").mkdir(parents=True)
    (source / "real" / "data.jsonl").write_text("{}", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "data.jsonl").write_text("{}", encoding="utf-8")
    link = source / "linked"
    if not _make_junction(link, outside):
        pytest.skip("当前环境无法创建 junction")
    try:
        assert WS.is_linked(link) is True
        assert WS.is_linked(source / "real") is False
        name = WS.add_folder(source)
        assert WS.source_files(name) == [source / "real" / "data.jsonl"]
    finally:
        os.rmdir(link)


# ── 后缀筛选必须发生在 limit 之前（避免 500 个非目标文件挤掉真数据集） ────────
def test_source_inventory_suffix_filter_before_limit(workspace_root, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    (source / "a.txt").write_text("x", encoding="utf-8")
    (source / "b.txt").write_text("x", encoding="utf-8")
    (source / "c.jsonl").write_text("{}", encoding="utf-8")
    name = WS.add_folder(source)
    assert WS.source_files(name, limit=1) == [source / "a.txt"]  # 无筛选：名字序取第一个
    assert WS.source_files(name, limit=1, suffixes=(".jsonl",)) == [source / "c.jsonl"]
    assert WS.source_files(name, suffixes=("jsonl",)) == [source / "c.jsonl"]  # 可不带点
    assert WS.source_files(name, suffixes={".jsonl", ".txt"}) == [
        source / "a.txt", source / "b.txt", source / "c.jsonl"]
    for bad in (".jsonl", (), [".jsonl", 1], [""]):
        with pytest.raises(ValueError):
            WS.source_files(name, suffixes=bad)


# ── 遗留状态损坏：current 回落 default；gates 失败关闭（空 + warning，绝不放行） ──
def test_malformed_legacy_current_falls_back_to_default(workspace_root):
    WS.CURRENT_PATH.parent.mkdir(parents=True, exist_ok=True)
    for content in ("broken", "[]", '"default"', '{"workspace": 17}'):
        WS.CURRENT_PATH.write_text(content, encoding="utf-8")
        assert WS.current() == WS.DEFAULT
        assert WS.resolve() == WS.DEFAULT


def test_malformed_gates_state_fails_closed_with_warning(workspace_root, tmp_path):
    source = tmp_path / "input"
    source.mkdir()
    name = WS.add_folder(source)
    destination = WS.out(name)
    destination.mkdir(parents=True)
    state = destination / "gates_state.json"
    for content in ("broken", "[]", "true"):
        state.write_text(content, encoding="utf-8")
        info = WS.status(name)
        assert info["gates"] == {}          # 未通过，不放行
        assert info["warning"]
    state.write_text(json.dumps({"gates": {"G1": "approved"}}), encoding="utf-8")
    info = WS.status(name)
    assert info["gates"] == {"gates": {"G1": "approved"}}
    assert "warning" not in info
