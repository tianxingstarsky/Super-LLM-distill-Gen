"""DPO 增强离线测试（fake client 脚本化：三种构造法 + 合并去重）。"""
from __future__ import annotations

import json

from lib.dpo_enhance import merge_pairs, pair_id


class FakeClient:
    """按提示词脚本化；judge 对含 GOOD 的回答给 5 分、含 BAD 的给 1 分。"""

    def __init__(self):
        self.usage = {"calls": 0}
        self.answer_count = 0

    def chat(self, messages, **kwargs):
        self.usage["calls"] += 1
        content = messages[0]["content"]
        if "回答精炼专家" in content:
            return json.dumps({"critique": "太简短", "refined": "GOOD 过拟合是模型过度拟合训练噪声导致泛化下降的现象。"}, ensure_ascii=False)
        if "偏好数据生成专家" in content:
            return json.dumps({"answer": "BAD 水的沸点是 120 摄氏度。"}, ensure_ascii=False)
        if "审校员" in content and "correctness" in content:
            return json.dumps({"correctness": 5 if "GOOD" in content else 1, "alignment": 3, "efficiency": 3, "lesson_quality": 3, "keep": True}, ensure_ascii=False)
        # 普通生成：交替 GOOD/BAD
        self.answer_count += 1
        return "GOOD 回答" if self.answer_count % 2 == 1 else "BAD 回答"


def test_candidates_pairs_only_with_gap():
    from lib.dpo_enhance import candidates

    pairs = candidates(FakeClient(), ["什么是过拟合"], n_per_prompt=3)
    assert len(pairs) == 1
    assert pairs[0]["chosen"][0]["content"] == "GOOD 回答"
    assert pairs[0]["rejected"][0]["content"] == "BAD 回答"
    assert pairs[0]["source"] == "candidates"


def test_refine_pair_chosen_is_refined():
    from lib.dpo_enhance import refine

    class RefineClient(FakeClient):
        def chat(self, messages, **kwargs):
            content = messages[0]["content"]
            if "回答精炼专家" in content:
                return json.dumps({"critique": "太简短", "refined": "GOOD 精炼后的回答。"}, ensure_ascii=False)
            if "审校员" in content:
                return json.dumps({"correctness": 5 if "GOOD" in content else 1, "alignment": 3, "efficiency": 3, "lesson_quality": 3, "keep": True}, ensure_ascii=False)
            return "BAD 初版回答"

    pairs = refine(RefineClient(), ["解释过拟合"])
    assert len(pairs) == 1
    assert "GOOD" in pairs[0]["chosen"][0]["content"]
    assert "BAD" in pairs[0]["rejected"][0]["content"]
    assert pairs[0]["source"] == "refine"


def test_hallucinate_pair_rejected_is_wrong():
    from lib.dpo_enhance import hallucinate

    items = [{"prompt": "水的沸点？", "answer": "GOOD 标准大气压下 100 摄氏度。", "facts": "沸点 100 度"}]
    pairs = hallucinate(FakeClient(), items)
    assert len(pairs) == 1
    assert "BAD" in pairs[0]["rejected"][0]["content"]
    assert "GOOD" in pairs[0]["chosen"][0]["content"]
    assert pairs[0]["source"] == "hallucinate"


def test_merge_pairs_dedups_and_normalizes():
    p1 = {"id": "a", "prompt": [{"role": "user", "content": "q"}], "chosen": [{"role": "assistant", "content": "c"}], "rejected": [{"role": "assistant", "content": "r"}]}
    p2 = dict(p1)  # 重复
    p3 = {"prompt": [{"role": "user", "content": "q2"}], "chosen": [{"role": "assistant", "content": "c2"}], "rejected": [{"role": "assistant", "content": "r2"}]}
    merged = merge_pairs([p1, p2, p3])
    assert len(merged) == 2
    assert all(set(m) == {"id", "prompt", "chosen", "rejected"} for m in merged)


def test_pair_id_deterministic():
    assert pair_id("q", "c", "r") == pair_id("q", "c", "r")
