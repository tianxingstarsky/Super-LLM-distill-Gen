"""长度控制：token 估算 + 长度 profile（合成数据的目标长度与混合配比）。

诚实边界（docs/length-guide.md）：合成数据单次输出数千 token（模型上限），
多轮拼接可到数万但质量衰减；百万上下文级样本只能来自真实 agent 长对话
（rollout 蒸馏正是该来源）。本模块负责"短中长"目标长度的受控生成。
"""
from __future__ import annotations

import pathlib
import re
from typing import Any, Dict, List

import yaml

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def estimate_tokens(text: str) -> int:
    """粗略 token 估算：CJK 字符 ≈1 token，其余按单词 ≈1.3 token。"""
    cjk = len(_CJK_RE.findall(text))
    others = re.sub(r"[\u4e00-\u9fff]", " ", text)
    words = len(others.split())
    return int(cjk + words * 1.3)


def load_profiles(path: str | pathlib.Path) -> Dict[str, Any]:
    return yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8"))


def length_note(tokens: int) -> str:
    """注入到生成提示词的长度要求（追加式，不改变提示词结构）。"""
    return f"（回答长度要求：约 {tokens} tokens，按需展开细节但不得注水）"


def sample_length(profiles: Dict[str, Any], profile: str) -> int:
    """按 profile 取目标长度：固定档直接返回；mixed 按权重随机采样。"""
    fixed = profiles.get("profiles", {}).get(profile)
    if fixed is not None:
        return int(fixed)
    mix = profiles.get("mix", {})
    if profile == "mixed" and mix:
        import random

        keys, weights = zip(*[(k, v) for k, v in mix.items()])
        picked = random.choices(keys, weights=weights, k=1)[0]
        return int(profiles["profiles"][picked])
    return 0  # 0 = 不注入长度要求
