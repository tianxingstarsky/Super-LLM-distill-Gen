"""Langfuse 监控：管线运行 trace（导入/蒸馏/导出 + token 成本 + 闸门状态）。

未配置 Langfuse（backends.local.yaml 无 langfuse 段或环境变量缺）时静默降级为
no-op，仅把同样的结构化记录写入 data/output/runs.jsonl（本地兜底审计）。
"""
from __future__ import annotations

import json
import os
import pathlib
import time
from typing import Any, Dict, Optional

LOCAL_RUNS = "data/output/runs.jsonl"


def _langfuse_config(root: pathlib.Path) -> Optional[Dict[str, str]]:
    import yaml

    local = root / "configs" / "backends.local.yaml"
    if not local.exists():
        return None
    cfg = yaml.safe_load(local.read_text(encoding="utf-8")) or {}
    lf = cfg.get("langfuse") or {}
    keys = {
        "public_key": lf.get("public_key") or os.environ.get("LANGFUSE_PUBLIC_KEY", ""),
        "secret_key": lf.get("secret_key") or os.environ.get("LANGFUSE_SECRET_KEY", ""),
        "host": lf.get("host") or os.environ.get("LANGFUSE_HOST", "http://localhost:3000"),
    }
    return keys if keys["public_key"] and keys["secret_key"] else None


def trace_run(root: pathlib.Path, kind: str, payload: Dict[str, Any]) -> None:
    """记录一次管线运行：Langfuse trace（如配置）+ 本地 runs.jsonl（永远写入）。"""
    entry: Dict[str, Any] = {"kind": kind, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), **payload}
    local_path = root / LOCAL_RUNS
    local_path.parent.mkdir(parents=True, exist_ok=True)
    with open(local_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    cfg = _langfuse_config(root)
    if not cfg:
        return
    try:
        from langfuse import Langfuse

        lf = Langfuse(public_key=cfg["public_key"], secret_key=cfg["secret_key"], host=cfg["host"])
        trace = lf.trace(name=f"df-{kind}", input=entry)
        if payload.get("usage"):
            trace.generation(name=kind, model=payload.get("model", ""), usage=payload["usage"])
        lf.flush()
    except Exception:  # noqa: BLE001 —— 监控失败不影响主流程
        pass
