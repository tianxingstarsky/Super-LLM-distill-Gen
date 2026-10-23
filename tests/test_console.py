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
    pages = ["总览", "资产管理", "数据预览", "管线运行", "人工审核", "质量报告", "监控", "模型与密钥", "闸门", "偏好设置"]
    for page in pages:
        assert page in text, f"缺页面 {page}"
    # 灵活性：管线运行页面必须覆盖主要命令、审核页面复用 render、偏好页面可编辑 yaml
    assert "_command_meta" in text and "load_commands" in text
    assert "render_message_sequence" in text
    assert "render_generation_settings" in text


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
    """命令注册表覆盖产品 CLI；外部控制器另行消费版本化协议。"""
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
    cli_commands -= {"df", "h", "console", "commands", "ws"}
    assert cli_commands <= set(cmds), f"CLI 有但注册表缺: {cli_commands - set(cmds)}"



def test_asset_categorization_logic():
    from lib.domain.dataset_assets import output_category

    assert output_category("rollout_samples.jsonl") == "样本"
    assert output_category("dpo_all.jsonl") == "DPO 偏好对"
    assert output_category("corpus_docs.jsonl") == "语料"
    assert output_category("budget.json") == "报告与状态"
