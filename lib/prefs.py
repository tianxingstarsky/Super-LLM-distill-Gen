"""偏好中心 v1：软配比加权采样 + 后验校正（纯数学采样，无智能）。

原则（与 preferences.yaml 一致）：偏好只调整"生成哪类数据、各占多少"，
绝不写入指令文本；default 兜底占比有下限，防风格坍缩/模板重复。
"""
from __future__ import annotations

import random
from typing import Dict, List, Optional

DEFAULT_FLOOR = 0.15


class PreferenceSampler:
    """按维度权重抽样 recipe 维度；支持按批次实际占比做软校正。"""

    def __init__(self, weights: Dict[str, float], default_floor: float = DEFAULT_FLOOR, seed: Optional[int] = None):
        if "default" not in weights:
            weights = {**weights, "default": 0.0}
        if weights["default"] < default_floor:
            weights = {**weights, "default": default_floor}
        total = sum(weights.values()) or 1.0
        self.weights = {k: v / total for k, v in weights.items()}  # 归一化
        self.rng = random.Random(seed)

    def sample(self, k: int) -> List[str]:
        dims = list(self.weights.keys())
        probs = [self.weights[d] for d in dims]
        return self.rng.choices(dims, weights=probs, k=k)

    @staticmethod
    def actual_distribution(draws: List[str]) -> Dict[str, float]:
        n = len(draws) or 1
        return {d: draws.count(d) / n for d in set(draws)}

    def corrected_weights(self, actual: Dict[str, float], strength: float = 1.0) -> Dict[str, float]:
        """后验校正：低于目标的维度下批加权（偏差×强度），再归一化并守住 default 下限。"""
        corrected = {}
        for dim, target in self.weights.items():
            got = actual.get(dim, 0.0)
            corrected[dim] = target * (1.0 + max(0.0, target - got) * strength)
        total = sum(corrected.values()) or 1.0
        out = {k: v / total for k, v in corrected.items()}
        # default 下限：抬高 default 后其余维度按比例压缩（不会再次稀释下限）
        floor = DEFAULT_FLOOR
        if "default" in out and out["default"] < floor:
            rest_total = 1.0 - floor
            other_total = sum(v for k, v in out.items() if k != "default") or 1.0
            out = {
                k: (floor if k == "default" else v / other_total * rest_total)
                for k, v in out.items()
            }
        return out
