"""提示词资产库：所有提示词统一入库，带版本、出处、防呆约束声明。

设计原则（M2 起生效）：
  1. 任何提示词改动 = 新版本，不覆盖旧版本（id@version 可追溯）。
  2. 每个提示词声明 variables（占位符）与 constraints（防呆约束）——
     结构契约测试（tests/test_prompts.py）与真机评测（df prompt-eval）都基于它们。
  3. source 标注出处：论文（arXiv）/ 上游项目，便于审计与引用。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass(frozen=True)
class PromptSpec:
    id: str
    version: str
    purpose: str
    source: str
    template: str
    variables: Tuple[str, ...] = ()
    constraints: Tuple[str, ...] = ()
    notes: str = ""


class PromptRenderError(ValueError):
    """渲染失败：变量缺失或未声明。"""


def render(spec: PromptSpec, **kwargs) -> str:
    """按声明变量渲染提示词；缺变量或多余变量一律报错（fail fast）。"""
    missing = [v for v in spec.variables if v not in kwargs]
    if missing:
        raise PromptRenderError(f"prompt {spec.id}@{spec.version} 缺少变量: {missing}")
    extra = [k for k in kwargs if k not in spec.variables]
    if extra:
        raise PromptRenderError(f"prompt {spec.id}@{spec.version} 未声明变量: {extra}")
    return spec.template.format(**kwargs)


def registry() -> Dict[str, PromptSpec]:
    """全量提示词注册表（id → 最新版 spec）。"""
    from lib.prompts import distill, judge, magpie, translation

    specs: Dict[str, PromptSpec] = {}
    for mod in (magpie, distill, judge, translation):
        for name in dir(mod):
            spec = getattr(mod, name)
            if isinstance(spec, PromptSpec):
                specs[spec.id] = spec
    return specs


def get(prompt_id: str) -> PromptSpec:
    specs = registry()
    if prompt_id not in specs:
        raise KeyError(f"未注册的提示词: {prompt_id}；已注册: {sorted(specs)}")
    return specs[prompt_id]
