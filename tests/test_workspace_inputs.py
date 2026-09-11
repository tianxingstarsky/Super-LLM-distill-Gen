"""所选文件夹输入路由：import/distill 默认读 WS.folder()（default 保留历史回退），
translate 默认 所选文件夹/topics.txt；--rollout-dir/--input 显式覆盖；枚举排除 .dataforge（无自我摄取）。
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "rollout_sample.jsonl"

# 与 lib/cli.cmd_import 相同：scripts 加入 sys.path 后按顶层模块名导入
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import import_rollout  # noqa: E402


# ── 夹具：隔离工作区注册表（绝不读写真实 data/workspaces.json） ──────────────
@pytest.fixture
def ws_env(tmp_path, monkeypatch):
    from lib import workspace as WS

    monkeypatch.setattr(WS, "REGISTRY_PATH", tmp_path / "registry" / "workspaces.json")
    monkeypatch.setattr(WS, "WORKSPACES_DIR", tmp_path / "registry" / "legacy")
    monkeypatch.setattr(WS, "CURRENT_PATH", tmp_path / "registry" / "legacy" / "current.json")
    return WS


class _Gate:
    """闸门替身：不落盘、不读真实 gates_state，只记录提案。"""

    def __init__(self, status: str = "approved"):
        self.state = status
        self.proposed: list[tuple[str, dict]] = []

    def status(self, gate_id: str) -> str:
        return self.state

    def propose(self, gate_id: str, context=None, note: str = "") -> None:
        self.proposed.append((gate_id, dict(context or {})))

    def require(self, gate_id: str) -> None:
        return None


def _open_folder(WS, folder: pathlib.Path, name: str) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    assert WS.add_folder(folder, name) == name
    return name


def _rollout_file(folder: pathlib.Path, name: str = "model-io-sess_ws.jsonl") -> pathlib.Path:
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    shutil.copyfile(FIXTURE, target)
    return target


def _run_import(cli, monkeypatch, out_dir: pathlib.Path, **overrides):
    monkeypatch.setattr(cli, "OUT_DIR", out_dir)
    monkeypatch.setattr(cli, "_gates", lambda: _Gate("approved"))
    monkeypatch.setattr("lib.monitor.trace_run", lambda *a, **k: None)
    args = SimpleNamespace(limit=0, export_limit=10, cot="separated", ws=None, rollout_dir=None)
    for key, value in overrides.items():
        setattr(args, key, value)
    assert cli.cmd_import(args) == 0
    return json.loads((out_dir / "rollout_stats.json").read_text(encoding="utf-8"))


# ── 路由优先级 ──────────────────────────────────────────────────────────────
def test_rollout_source_routing_priority(ws_env, tmp_path, monkeypatch):
    from lib import cli

    opened = tmp_path / "opened" / "dataset"
    opened.mkdir(parents=True)
    _open_folder(ws_env, opened, "docs-src")
    explicit = tmp_path / "explicit"
    explicit.mkdir()

    # 非 default 文件夹 → 所选文件夹（不是个人 rollout 目录）
    monkeypatch.setenv("DF_WORKSPACE", "docs-src")
    assert cli._rollout_source(SimpleNamespace(ws=None, rollout_dir=None)) == opened
    # 显式 --rollout-dir 最高优先
    assert cli._rollout_source(SimpleNamespace(ws=None, rollout_dir=str(explicit))) == explicit
    # 显式 --ws 无需环境变量
    monkeypatch.delenv("DF_WORKSPACE", raising=False)
    assert cli._rollout_source(SimpleNamespace(ws="docs-src", rollout_dir=None)) == opened

    # default → None：交给 scripts/import_rollout 按 ROLLOUT_DIR/配置解析（历史兼容）
    monkeypatch.setenv("DF_WORKSPACE", "default")
    assert cli._rollout_source(SimpleNamespace(ws=None, rollout_dir=None)) is None

    # 写错的显式目录要报错，不能静默换源
    with pytest.raises(ValueError, match="数据源目录不存在"):
        cli._rollout_source(SimpleNamespace(ws=None, rollout_dir=str(tmp_path / "missing")))


# ── import：非 default 读所选文件夹，不读个人目录/环境历史目录 ───────────────
def test_import_reads_selected_folder_not_legacy_dir(ws_env, tmp_path, monkeypatch):
    from lib import cli

    opened = tmp_path / "opened"
    _rollout_file(opened, "model-io-sess_selected.jsonl")
    _open_folder(ws_env, opened, "docs-import")
    legacy = tmp_path / "personal-rollout"
    _rollout_file(legacy, "model-io-sess_legacy.jsonl")
    monkeypatch.setenv("ROLLOUT_DIR", str(legacy))
    monkeypatch.setenv("DF_WORKSPACE", "docs-import")

    out = tmp_path / "out"
    stats = _run_import(cli, monkeypatch, out)
    assert list(stats) == ["model-io-sess_selected.jsonl"]
    assert len((out / "rollout_samples.jsonl").read_text(encoding="utf-8").splitlines()) == 3


def test_import_explicit_rollout_dir_overrides_folder(ws_env, tmp_path, monkeypatch):
    from lib import cli

    opened = tmp_path / "opened"
    _rollout_file(opened, "model-io-sess_from-ws.jsonl")
    _open_folder(ws_env, opened, "docs-explicit")
    monkeypatch.setenv("DF_WORKSPACE", "docs-explicit")
    explicit = tmp_path / "explicit"
    _rollout_file(explicit, "model-io-sess_from-explicit.jsonl")

    stats = _run_import(cli, monkeypatch, tmp_path / "out", rollout_dir=str(explicit))
    assert list(stats) == ["model-io-sess_from-explicit.jsonl"]


# ── default：保留 ROLLOUT_DIR 环境变量/配置回退 ─────────────────────────────
def test_import_default_keeps_legacy_env_fallback(ws_env, tmp_path, monkeypatch):
    from lib import cli

    opened = tmp_path / "opened"
    _rollout_file(opened, "model-io-sess_from-ws.jsonl")
    _open_folder(ws_env, opened, "docs-other")
    legacy = tmp_path / "personal-rollout"
    _rollout_file(legacy, "model-io-sess_legacy.jsonl")
    monkeypatch.setenv("ROLLOUT_DIR", str(legacy))
    monkeypatch.setenv("DF_WORKSPACE", "default")
    # 顶层 import_rollout 可能已被其他测试导入（模块级解析），显式对齐本次环境
    monkeypatch.setattr(import_rollout, "ROLLOUT_DIR", legacy)

    stats = _run_import(cli, monkeypatch, tmp_path / "out")
    assert list(stats) == ["model-io-sess_legacy.jsonl"]


def test_legacy_rollout_dir_env_resolution_in_fresh_process(tmp_path):
    """全新进程：ROLLOUT_DIR 环境变量压过 configs/pipelines/rollout.yaml（历史回退不变）。"""
    env = dict(os.environ)
    env["ROLLOUT_DIR"] = str(tmp_path / "env-dir")
    env["PYTHONPATH"] = str(ROOT)
    code = "import sys; sys.path.insert(0, 'scripts'); import import_rollout; print(import_rollout.ROLLOUT_DIR)"
    result = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(tmp_path / "env-dir")


# ── 枚举排除 .dataforge 产物（输出目录就在所选文件夹内也不自我摄取） ────────
def test_import_never_ingests_dataforge_outputs(ws_env, tmp_path, monkeypatch):
    from lib import cli
    import scripts.import_rollout as importer

    opened = tmp_path / "opened"
    real = _rollout_file(opened, "model-io-sess_real.jsonl")
    _rollout_file(opened / ".dataforge" / "output", "model-io-sess_self.jsonl")
    _open_folder(ws_env, opened, "docs-self")
    monkeypatch.setenv("DF_WORKSPACE", "docs-self")

    assert importer.source_files(opened) == [real]

    out = opened / ".dataforge" / "output"  # 产物目录即所选文件夹内（真实路由）
    first = _run_import(cli, monkeypatch, out)
    assert list(first) == ["model-io-sess_real.jsonl"]
    samples = (out / "rollout_samples.jsonl").read_text(encoding="utf-8")

    # 增量导入兼容：二次运行 manifest 去重、零重复追加
    second = _run_import(cli, monkeypatch, out)
    assert list(second) == ["model-io-sess_real.jsonl"]
    assert (out / "rollout_samples.jsonl").read_text(encoding="utf-8") == samples


def test_source_files_recursive_excluding_dataforge_and_legacy_top_level(tmp_path, monkeypatch):
    import scripts.import_rollout as importer

    monkeypatch.setattr(importer, "ROLLOUT_PATTERN", "model-io-sess_*.jsonl")
    root = tmp_path / "data"
    nested = root / "sub"
    nested.mkdir(parents=True)
    top = root / "model-io-sess_top.jsonl"
    top.write_text("{}", encoding="utf-8")
    deep = nested / "model-io-sess_deep.jsonl"
    deep.write_text("{}", encoding="utf-8")
    generated = root / ".dataforge" / "output" / "model-io-sess_self.jsonl"
    generated.parent.mkdir(parents=True)
    generated.write_text("{}", encoding="utf-8")
    (root / "notes.txt").write_text("x", encoding="utf-8")

    assert importer.source_files(root) == [top, deep]  # 目录：递归 + 排除 .dataforge
    assert importer.source_files(top) == [top]  # 文件：直接作为唯一源
    assert importer.source_files(root / "missing") == []
    # None = 历史行为（仅顶层 glob），与 test_reliability 的增量导入用法兼容
    monkeypatch.setattr(importer, "ROLLOUT_DIR", root)
    assert importer.source_files(None) == [top]


# ── 链接目录/文件：两个扫描器共用 WS.is_linked（3.11 无 is_junction，靠 reparse tag） ──
def _make_junction(link: pathlib.Path, target: pathlib.Path) -> bool:
    """Windows 下安全创建 junction（免管理员；失败返回 False，由调用方 skip）。"""
    if os.name != "nt":
        return False
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                            capture_output=True)
    return result.returncode == 0


@pytest.mark.skipif(os.name != "nt", reason="junction 仅 Windows")
def test_both_scanners_skip_junctions(ws_env, tmp_path):
    import scripts.import_rollout as importer

    opened = tmp_path / "opened"
    real = _rollout_file(opened, "model-io-sess_real.jsonl")
    outside = tmp_path / "outside"
    _rollout_file(outside, "model-io-sess_outside.jsonl")
    link = opened / "linked"
    if not _make_junction(link, outside):
        pytest.skip("当前环境无法创建 junction")
    try:
        assert importer.source_files(opened) == [real]           # import/distill 扫描器
        name = ws_env.add_folder(opened, name="docs-junction")
        assert ws_env.source_files(name) == [real]               # 控制台清点扫描器
    finally:
        os.rmdir(link)


# ── 递归导入统计用相对路径：不同子目录同名文件不再互相覆盖（default 保持 basename） ──
def test_recursive_import_stats_use_relative_paths(ws_env, tmp_path, monkeypatch):
    from lib import cli

    opened = tmp_path / "opened"
    _rollout_file(opened / "a", "model-io-sess_same.jsonl")
    _rollout_file(opened / "b", "model-io-sess_same.jsonl")
    _open_folder(ws_env, opened, "docs-stats")
    monkeypatch.setenv("DF_WORKSPACE", "docs-stats")

    stats = _run_import(cli, monkeypatch, tmp_path / "out")
    assert sorted(stats) == ["a/model-io-sess_same.jsonl", "b/model-io-sess_same.jsonl"]


# ── G1 提案必须记录实际数据源 ───────────────────────────────────────────────
def test_g1_proposal_uses_actual_source(ws_env, tmp_path, monkeypatch):
    from lib import cli

    opened = tmp_path / "opened"
    _rollout_file(opened, "model-io-sess_ws.jsonl")
    _open_folder(ws_env, opened, "docs-gate")
    monkeypatch.setenv("DF_WORKSPACE", "docs-gate")
    explicit = tmp_path / "explicit"
    explicit.mkdir()

    gate = _Gate("awaiting")
    monkeypatch.setattr(cli, "_gates", lambda: gate)
    base = dict(limit=0, export_limit=1, cot="separated", ws=None, rollout_dir=None)
    with pytest.raises(cli.GateBlocked):
        cli.cmd_import(SimpleNamespace(**base))
    assert pathlib.Path(gate.proposed[0][1]["rollout_dir"]) == opened

    gate2 = _Gate("pending")
    monkeypatch.setattr(cli, "_gates", lambda: gate2)
    with pytest.raises(cli.GateBlocked):
        cli.cmd_import(SimpleNamespace(**{**base, "rollout_dir": str(explicit)}))
    assert pathlib.Path(gate2.proposed[0][1]["rollout_dir"]) == explicit

    # default 工作区：提案写历史回退目录（环境变量/配置解析结果），而不是空或猜测值
    monkeypatch.setenv("DF_WORKSPACE", "default")
    legacy = tmp_path / "legacy-dir"
    monkeypatch.setattr(import_rollout, "ROLLOUT_DIR", legacy)
    gate3 = _Gate("pending")
    monkeypatch.setattr(cli, "_gates", lambda: gate3)
    with pytest.raises(cli.GateBlocked):
        cli.cmd_import(SimpleNamespace(**base))
    assert pathlib.Path(gate3.proposed[0][1]["rollout_dir"]) == legacy


# ── distill：DPO 提取同源路由 ───────────────────────────────────────────────
def test_distill_reads_selected_folder_and_explicit_dir(ws_env, tmp_path, monkeypatch):
    from lib import cli
    import lib.adapters.distill as distill_mod

    opened = tmp_path / "opened"
    ws_file = _rollout_file(opened, "model-io-sess_selected.jsonl")
    _open_folder(ws_env, opened, "docs-distill")
    legacy = tmp_path / "personal-rollout"
    _rollout_file(legacy, "model-io-sess_legacy.jsonl")
    monkeypatch.setenv("ROLLOUT_DIR", str(legacy))
    monkeypatch.setenv("DF_WORKSPACE", "docs-distill")

    out = tmp_path / "out"
    _run_import(cli, monkeypatch, out)  # 先有本区样本

    seen: list[pathlib.Path] = []

    def fake_extract(path, style):
        seen.append(pathlib.Path(path))
        return [{"prompt": [], "chosen": [], "rejected": []}]

    monkeypatch.setattr(distill_mod, "extract_dpo_pairs", fake_extract)
    monkeypatch.setattr(cli, "_gates", lambda: _Gate("approved"))
    monkeypatch.setattr("lib.monitor.trace_run", lambda *a, **k: None)

    args = SimpleNamespace(llm_check=0, backend=None, model=None, ws=None, rollout_dir=None)
    assert cli.cmd_distill(args) == 0
    assert seen == [ws_file]

    seen.clear()
    explicit = tmp_path / "explicit"
    explicit_file = _rollout_file(explicit, "model-io-sess_explicit.jsonl")
    assert cli.cmd_distill(SimpleNamespace(llm_check=0, backend=None, model=None, ws=None,
                                           rollout_dir=str(explicit))) == 0
    assert seen == [explicit_file]

    report = json.loads((out / "distill_report.json").read_text(encoding="utf-8"))
    assert report["n_dpo_pairs"] == 1
    assert (out / "dpo_pairs.jsonl").exists()


# ── translate：默认 所选文件夹/topics.txt，--input 显式保留，default 历史不变 ─
def _fake_translate_deps(cli, translator, monkeypatch, captured):
    def fake_run(client, lines, limit, **kwargs):
        captured["lines"] = lines
        return []

    monkeypatch.setattr(cli, "_gates", lambda: _Gate("approved"))
    monkeypatch.setattr(cli, "_client", lambda args, **kwargs: (SimpleNamespace(usage={}), "mock-model"))
    monkeypatch.setattr(translator, "run_translation", fake_run)


def test_translate_uses_selected_folder_topics_and_explicit_input(ws_env, tmp_path, monkeypatch):
    from lib import cli
    import lib.translator as translator

    opened = tmp_path / "opened"
    opened.mkdir()
    (opened / "topics.txt").write_text("主题甲\n主题乙\n", encoding="utf-8")
    _open_folder(ws_env, opened, "docs-tr")
    monkeypatch.setenv("DF_WORKSPACE", "docs-tr")
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(cli, "OUT_DIR", out)
    captured: dict = {}
    _fake_translate_deps(cli, translator, monkeypatch, captured)

    assert cli.cmd_translate(SimpleNamespace(input=None, limit=5, backend=None, model=None, ws=None)) == 0
    assert captured["lines"] == ["主题甲", "主题乙"]
    assert (out / "translation_pairs.jsonl").exists()

    custom = tmp_path / "custom.txt"
    custom.write_text("only one\n", encoding="utf-8")
    assert cli.cmd_translate(SimpleNamespace(input=str(custom), limit=5, backend=None,
                                             model=None, ws=None)) == 0
    assert captured["lines"] == ["only one"]

    # 所选文件夹缺 topics.txt → 明确报错，不静默改用其他来源
    empty_ws = tmp_path / "empty"
    _open_folder(ws_env, empty_ws, "docs-empty")
    monkeypatch.setenv("DF_WORKSPACE", "docs-empty")
    with pytest.raises(FileNotFoundError, match="topics.txt"):
        cli.cmd_translate(SimpleNamespace(input=None, limit=5, backend=None, model=None, ws=None))


def test_translate_default_keeps_legacy_topics(ws_env, tmp_path, monkeypatch):
    from lib import cli
    import lib.translator as translator

    topics = ROOT / "data" / "seeds" / "topics.txt"
    assert topics.is_file()  # 仓库内置样例（只读）
    monkeypatch.setenv("DF_WORKSPACE", "default")
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setattr(cli, "OUT_DIR", out)
    captured: dict = {}
    _fake_translate_deps(cli, translator, monkeypatch, captured)

    assert cli.cmd_translate(SimpleNamespace(input=None, limit=5, backend=None, model=None, ws=None)) == 0
    assert captured["lines"] == topics.read_text(encoding="utf-8").splitlines()


# ── 解析器默认值 ────────────────────────────────────────────────────────────
def test_parser_input_flags():
    from lib import cli

    parser = cli.build_parser()
    assert parser.parse_args(["import"]).rollout_dir is None
    assert parser.parse_args(["import", "--rollout-dir", "X"]).rollout_dir == "X"
    assert parser.parse_args(["distill"]).rollout_dir is None
    assert parser.parse_args(["distill", "--rollout-dir", "Y"]).rollout_dir == "Y"
    assert parser.parse_args(["translate"]).input is None
    assert parser.parse_args(["translate", "--input", "Z"]).input == "Z"
