"""统一管线配置层：configs/pipelines/*.yaml 集中参数，CLI 参数 > yaml > 内置默认。"""
from __future__ import annotations

import pathlib
from typing import Any, Dict

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent

# 内置默认（yaml 可覆盖；CLI 参数再覆盖）
DEFAULTS: Dict[str, Any] = {
    "llm": {"temperature": 0.7, "thinking": True, "json_mode": False, "retries": 3, "max_tokens": None},
    "translation": {"faithful_threshold": 4, "temperature": 0.3},
    "doc2data": {"qa_per_chunk": 3, "max_chunks": 5, "chunk_size": 2000, "min_chunk": 40, "ground_check": True, "temperature": 0.8},
    "doc2corpus": {"chunk_size": 2000, "overlap": 0},
    "distill": {"llm_check_n": 5, "judge_temperature": 0.2},
    "review": {"pass_threshold": 0.9, "min_reviewed": 10},
    "rollout": {"dir": r"C:\Users\tianx\.zcode\cli\rollout", "pattern": "model-io-sess_*.jsonl", "truncate_chars": 8000},
}


def load_pipeline_config(name: str, root: pathlib.Path | None = None, overrides: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """加载 configs/pipelines/{name}.yaml，合并内置默认与调用方覆盖。"""
    root = root or ROOT
    cfg = dict(DEFAULTS)
    path = root / "configs" / "pipelines" / f"{name}.yaml"
    if path.exists():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        _deep_merge(cfg, loaded)
    if overrides:
        _deep_merge(cfg, overrides)
    return cfg


def _deep_merge(base: Dict[str, Any], extra: Dict[str, Any]) -> None:
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v
