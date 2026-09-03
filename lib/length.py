"""长度控制（上限守卫语义）：只做截断保护，不做目标长度注入。

设计（用户确认）：长度不是生成目标——解决问题操作越少越好、无用输出越少越好。
长数据靠任务性质自然长：知识学习（文档/文章/代码综合分析）、真实 agent 长对话
（rollout）、多步轨迹；本模块只提供 max_tokens 截断上限作为工程保护。
"""
from __future__ import annotations

import pathlib
import re
from typing import Dict

import yaml

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def estimate_tokens(text: str) -> int:
    """粗略 token 估算：CJK 字符 ≈1 token，其余按单词 ≈1.3 token。"""
    cjk = len(_CJK_RE.findall(text))
    others = re.sub(r"[\u4e00-\u9fff]", " ", text)
    words = len(others.split())
    return int(cjk + words * 1.3)


def truncate_to_max(text: str, max_tokens: int) -> str:
    """按估算 token 数截断（保护性上限；截断处加省略标记，后缀预算预留）。"""
    if max_tokens <= 0 or estimate_tokens(text) <= max_tokens:
        return text
    suffix = "…[截断]"
    budget = max_tokens - estimate_tokens(suffix)  # 后缀预留
    if budget <= 0:
        return suffix
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if estimate_tokens(text[:mid]) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + suffix


def load_profiles(path: str | pathlib.Path) -> Dict:
    return yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8"))
