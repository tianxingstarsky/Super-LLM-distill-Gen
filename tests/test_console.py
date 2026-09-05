"""统一控制台契约测试：页面完整性 + CLI 接线 + 渲染函数可复用。"""
from __future__ import annotations

import pathlib
import py_compile
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEBAPP = ROOT / "lib" / "webapp.py"


def test_webapp_compiles():
    py_compile.compile(str(WEBAPP), doraise=True)


def test_console_pages_cover_all_operations():
    text = WEBAPP.read_text(encoding="utf-8")
    pages = ["总览", "数据预览", "管线运行", "人工审核", "监控", "模型与闸门", "偏好设置"]
    for page in pages:
        assert page in text, f"缺页面 {page}"
    # 灵活性：管线运行页面必须覆盖主要命令、审核页面复用 render、偏好页面可编辑 yaml
    assert "_command_meta" in text and "load_commands" in text
    assert "_render_message" in text
    assert "PREF_FILES" in text and "cot_styles" in text


def test_console_command_wired_in_cli():
    cli_text = (ROOT / "lib" / "cli.py").read_text(encoding="utf-8")
    assert 'add_parser("console"' in cli_text
    # 命令表与插件交叉校验不受影响（console 不作为 dataforge 工具命令）
    df_help = subprocess.run(
        [sys.executable, "-m", "lib.cli", "-h"], cwd=str(ROOT),
        capture_output=True, text=True, timeout=60,
    ).stdout
    assert "console" in df_help


def test_command_registry_single_source_of_truth():
    """命令注册表（configs/pipelines/commands.yaml）必须覆盖 CLI 全部子命令，
    且 dsh 插件命令表与之完全一致——单一事实源防漂移。"""
    from lib.extensions import load_commands

    cmds = load_commands(ROOT)
    assert len(cmds) >= 20

    # CLI 子命令（去本地 UI 启动器 console）必须都被注册表覆盖
    df_help = subprocess.run(
        [sys.executable, "-m", "lib.cli", "-h"], cwd=str(ROOT),
        capture_output=True, text=True, timeout=60,
    ).stdout
    block = df_help.split("usage:", 1)[-1].split(chr(10) * 2, 1)[0]
    import re as _re

    cli_commands = set(_re.findall(r"(?<![a-z0-9])[a-z][a-z0-9-]+(?![a-z0-9])", block))
    cli_commands -= {"df", "h", "console", "commands"}
    assert cli_commands <= set(cmds), f"CLI 有但注册表缺: {cli_commands - set(cmds)}"

    # dsh 插件命令表 == 注册表（插件暴露全部数据命令）
    import re as _re2

    plugin_keys = set(_re2.findall(r"^  '?([a-z][a-z0-9-]*)'?:",
                                   (ROOT / "plugins" / "dsh-dataforge" / "src" / "dataforge.ts").read_text(encoding="utf-8"), _re2.M))
    assert plugin_keys == set(cmds), f"插件与注册表漂移: 插件多 {plugin_keys - set(cmds)}，注册表多 {set(cmds) - plugin_keys}"


def test_asset_categorization_logic():
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        import pathlib as _p

        d = _p.Path(td)
        (d / "rollout_samples.jsonl").write_text("{}")
        (d / "dpo_all.jsonl").write_text("{}")
        (d / "corpus_docs.jsonl").write_text("{}")
        (d / "budget.json").write_text("{}")
        text = WEBAPP.read_text(encoding="utf-8")
        # 分类规则存在且覆盖四类
        for cat in ("样本", "DPO 偏好对", "语料", "报告与状态"):
            assert cat in text, f"缺分类 {cat}"
