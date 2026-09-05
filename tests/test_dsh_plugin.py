"""dsh 插件结构契约测试：插件格式对齐官方文档（docs/cookbook/adding-a-tool.md），
命令表与 lib/cli.py 子命令交叉校验（防漂移）。"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
PLUGIN_TS = ROOT / "plugins" / "dsh-dataforge" / "src" / "dataforge.ts"
SKILL = ROOT / "plugins" / "dsh-dataforge" / "skills" / "dataforge" / "SKILL.md"


def test_plugin_module_shape_matches_documented_format():
    text = PLUGIN_TS.read_text(encoding="utf-8")
    # 官方插件格式：export const name / export const inject = ['tools'] / export function apply(ctx)
    assert re.search(r"export const name = 'dataforge'", text)
    assert re.search(r"export const inject = \['tools'\]", text)
    assert re.search(r"export function apply\(ctx: Context\)", text)
    assert "defineTool(" in text
    # 调度工具必须有 command 参数与 execute
    assert "command: {" in text and "async execute(args, exec)" in text


def test_plugin_commands_match_cli_subcommands():
    """插件命令表必须与 df CLI 子命令一致（防止两边漂移）。"""
    df_help = subprocess.run(
        [sys.executable, "-m", "lib.cli", "-h"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=60,
    )
    # usage 行可能换行：取 "usage:" 到首个空行之间的块，提取全部小写命令 token
    block = df_help.stdout.split("usage:", 1)[-1].split("\n\n", 1)[0]
    noise = {"df", "h", "error", "unrecognized", "arguments", "usage", "ws"}  # ws=全局 --ws 选项
    cli_commands = set(re.findall(r"\b[a-z][a-z0-9-]+\b", block)) - noise
    # 本地 UI 启动器类命令不暴露给 agent（插件命令表不含它们是预期）
    cli_commands -= {"console", "commands"}
    plugin_commands = set(re.findall(r"^  '?([a-z][a-z0-9-]*)'?:", PLUGIN_TS.read_text(encoding="utf-8"), re.M))
    assert plugin_commands == cli_commands, f"漂移：插件多 {plugin_commands - cli_commands}，CLI 多 {cli_commands - plugin_commands}"


def test_plugin_positional_mapping_present():
    """位置参数映射必须覆盖 CLI 的位置参数命令（gate/review/review-remote 是 action 位置参数，
    不是 --action 选项；错传会像真机联调那样直接报 '--action 不是可识别参数'）。"""
    text = PLUGIN_TS.read_text(encoding="utf-8")
    assert "POSITIONAL_OPS" in text
    assert re.search(r"gate: \['action', 'gate_id'\]", text)
    assert re.search(r"review: \['action'\]", text)
    assert re.search(r"'review-remote': \['action'\]", text)


def test_skill_has_required_workflow_rules():
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---")  # frontmatter
    assert "name: dataforge" in text
    # 闸门铁律必须写进 skill（agent 不得擅自花 token/放量）
    assert "gate status" in text and "G0" in text and "G3" in text


def test_harness_submodule_pinned():
    gitmodules = (ROOT / ".gitmodules").read_text(encoding="utf-8")
    assert "components/deepseek-harness" in gitmodules
    assert (ROOT / "components" / "deepseek-harness" / "docs" / "cookbook" / "adding-a-tool.md").exists()
