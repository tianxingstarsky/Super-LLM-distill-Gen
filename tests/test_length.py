"""长度控制离线测试：token 估算 + profile 采样 + 注入格式。"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_estimate_tokens_mixed_language():
    from lib.length import estimate_tokens

    zh = estimate_tokens("机器学习模型在训练数据上拟合规律")
    assert zh >= 10  # 每个汉字 ≈1 token
    en = estimate_tokens("machine learning models learn patterns from data")
    assert 5 <= en <= 12


def test_sample_length_fixed_and_mixed():
    from lib.length import load_profiles, sample_length

    profiles = load_profiles(ROOT / "configs" / "pipelines" / "length_profiles.yaml")
    assert sample_length(profiles, "short") == 200
    assert sample_length(profiles, "xlong") == 8000
    for _ in range(30):
        n = sample_length(profiles, "mixed")
        assert n in (200, 800, 3000, 8000)  # 配比内采样


def test_length_note_format():
    from lib.length import length_note

    note = length_note(800)
    assert "800 tokens" in note and "注水" in note
