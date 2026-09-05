"""命令注册表（单一事实源）加载与校验：CLI/控制台/插件/测试四方共用。"""
from __future__ import annotations

import pathlib
from typing import Any, Dict

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent


def load_commands(root: pathlib.Path | None = None) -> Dict[str, Dict[str, Any]]:
    path = (root or ROOT) / "configs" / "pipelines" / "commands.yaml"
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("commands", {})


def groups(commands: Dict[str, Dict[str, Any]]) -> Dict[str, list]:
    out: Dict[str, list] = {}
    for name, meta in commands.items():
        out.setdefault(meta.get("group", "其他"), []).append(name)
    return out
