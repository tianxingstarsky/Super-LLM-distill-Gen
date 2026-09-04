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
    assert "COMMAND_HELP" in text and "identity-gen" in text
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
