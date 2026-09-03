"""偏好中心 v1 离线测试：软配比采样 + default 下限 + 后验校正。"""
from __future__ import annotations

from lib.prefs import PreferenceSampler

WEIGHTS = {"reasoning": 0.30, "context_use": 0.20, "tool_use": 0.25, "bilingual": 0.10, "default": 0.0}


def test_default_floor_enforced():
    s = PreferenceSampler(WEIGHTS, default_floor=0.15)
    assert s.weights["default"] >= 0.15  # 不可归零（防坍缩）
    total = sum(s.weights.values())
    assert abs(total - 1.0) < 1e-9


def test_sample_deterministic_and_counts():
    s = PreferenceSampler(WEIGHTS, seed=42)
    draws = s.sample(1000)
    assert len(draws) == 1000
    assert set(draws) <= set(WEIGHTS.keys())
    # 同种子复现
    s2 = PreferenceSampler(WEIGHTS, seed=42)
    assert s2.sample(1000) == draws


def test_soft_target_dominates():
    # 大样本下各维度占比应接近目标权重（软配比生效，而非等概率）
    s = PreferenceSampler(WEIGHTS, seed=7)
    draws = s.sample(20000)
    actual = PreferenceSampler.actual_distribution(draws)
    for dim, target in s.weights.items():
        assert abs(actual.get(dim, 0.0) - target) < 0.02, f"{dim}: {actual.get(dim)} vs {target}"


def test_correction_boosts_underrepresented():
    s = PreferenceSampler(WEIGHTS, seed=1)
    # 模拟某批次 tool_use 严重不足
    actual = {"reasoning": 0.5, "context_use": 0.3, "tool_use": 0.0, "bilingual": 0.05, "default": 0.15}
    corrected = s.corrected_weights(actual, strength=2.0)
    assert corrected["tool_use"] > s.weights["tool_use"]  # 不足的维度被加权
    assert corrected["default"] >= 0.15  # 下限仍守住
    assert abs(sum(corrected.values()) - 1.0) < 1e-9
