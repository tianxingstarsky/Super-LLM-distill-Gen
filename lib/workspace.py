"""工作区：数据按区分流，不挤一锅。

语义（商业级：不同项目/数据域/客户的批次天然隔离）：
  default 工作区 → data/output（完全向后兼容，已有数据与测试不动）
  其他工作区     → data/workspaces/<名>/output（闸门状态、样本、审核、导出全部隔离）
  Argilla 数据集 → rollout_review（default）/ rollout_review_<名>（其他）
  预算硬停（BudgetGuard）保持全局——钱是全局的，按工作区只隔离数据与闸门。

选择优先级：显式 --ws > 环境变量 DF_WORKSPACE > data/workspaces/current.json > default。
"""
from __future__ import annotations

import json
import os
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent

DEFAULT = "default"
WORKSPACES_DIR = ROOT / "data" / "workspaces"
CURRENT_PATH = WORKSPACES_DIR / "current.json"

# 安全名：防路径穿越/奇怪字符（工作区名会进文件系统路径与 Argilla 数据集名）
_SAFE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def validate(name: str) -> str:
    if not _SAFE.match(name or ""):
        raise ValueError(f"工作区名只允许字母/数字/下划线/连字符，长度 1-32：{name!r}")
    return name


def current() -> str:
    try:
        return validate(json.loads(CURRENT_PATH.read_text(encoding="utf-8")).get("workspace", DEFAULT))
    except (OSError, ValueError, json.JSONDecodeError):
        return DEFAULT


def set_current(name: str) -> str:
    name = validate(name)
    out(name)  # 顺带建目录
    WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
    CURRENT_PATH.write_text(json.dumps({"workspace": name}, ensure_ascii=False, indent=1), encoding="utf-8")
    return name


def resolve(ws: str | None = None) -> str:
    """工作区解析：显式参数 > DF_WORKSPACE > current.json > default。"""
    if ws:
        return validate(ws)
    env = os.environ.get("DF_WORKSPACE")
    if env:
        return validate(env)
    return current()


def out(ws: str | None = None) -> pathlib.Path:
    """工作区输出目录（default=原 data/output；其他在 data/workspaces/<名>/output）。"""
    name = resolve(ws)
    if name == DEFAULT:
        return ROOT / "data" / "output"
    d = WORKSPACES_DIR / name / "output"
    d.mkdir(parents=True, exist_ok=True)
    return d


# 别名：与 lib.cli 的 OUT_DIR 语义对齐
def out_dir(ws: str | None = None) -> pathlib.Path:
    return out(ws)


def dataset_name(ws: str | None = None) -> str:
    """Argilla 审核数据集名（default 工作区保持 rollout_review，兼容已审核数据）。"""
    name = resolve(ws)
    return "rollout_review" if name == DEFAULT else f"rollout_review_{name}"


def list_all() -> list[str]:
    """全部工作区名（default 恒在；其余为 data/workspaces 下的目录）。"""
    names = [DEFAULT]
    if WORKSPACES_DIR.exists():
        for d in sorted(WORKSPACES_DIR.iterdir()):
            if d.is_dir() and _SAFE.match(d.name) and d.name not in names:
                names.append(d.name)
    return names


def status(ws: str | None = None) -> dict:
    """工作区概览：输出目录、数据集名、闸门状态（供 CLI status 与控制台）。"""
    name = resolve(ws)
    d = out(name)
    gates: dict = {}
    state = d / "gates_state.json"
    if state.exists():
        try:
            gates = json.loads(state.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            gates = {}
    return {
        "workspace": name,
        "out_dir": str(d),
        "dataset": dataset_name(name),
        "gates": gates,
    }
