"""长度控制（上限守卫语义）离线测试。"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_estimate_tokens_mixed_language():
    from lib.length import estimate_tokens

    zh = estimate_tokens("机器学习模型在训练数据上拟合规律")
    assert zh >= 10  # 每个汉字 ≈1 token
    en = estimate_tokens("machine learning models learn patterns from data")
    assert 5 <= en <= 12


def test_truncate_to_max_guard_only():
    from lib.length import estimate_tokens, truncate_to_max

    short = "这是一段很短的文本。"
    assert truncate_to_max(short, 100) == short  # 未超上限不动
    long_text = "这是一个很长很长的回答。" * 500  # 估算远超上限
    capped = truncate_to_max(long_text, 100)
    assert estimate_tokens(capped) <= 100
    assert capped.endswith("…[截断]")
    assert truncate_to_max(long_text, 0) == long_text  # 0=不限


def test_profiles_are_limits_not_targets():
    from lib.length import load_profiles

    profiles = load_profiles(ROOT / "configs" / "pipelines" / "length_profiles.yaml")
    mc = profiles["max_context"]
    assert mc["answer_tokens"] > 0 and mc["sample_tokens"] > mc["answer_tokens"]
