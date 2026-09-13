"""风格强矫正离线测试（fake client 脚本化多轮收敛）。"""
from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _cfg():
    from lib.style_fix import load_rules

    return load_rules(ROOT / "configs" / "style_rules.example.yaml")


class FakeClient:
    """check：含 STYLE 的文本 adherence 5、否则 1；polish：每轮加一层 STYLE 前缀。"""

    def __init__(self):
        self.usage = {"calls": 0}

    def chat(self, messages, **kwargs):
        self.usage["calls"] += 1
        content = messages[0]["content"]
        if "语言风格审校员" in content:
            text = content.split("# 待审文本:")[-1]
            return json.dumps({"adherence": 5 if "STYLE" in text else 1, "violations": [], "keep": True}, ensure_ascii=False)
        if "语言风格矫正专家" in content:
            text = content.split("# 待矫正文本:")[-1].split("# 用户风格规则")[0].strip()
            return json.dumps({"corrected": "STYLE " + text, "changes": ["去模板词"]}, ensure_ascii=False)
        raise AssertionError("未知提示词")


def _samples():
    return [
        {"id": "s1", "source": "identity", "type": "sft", "messages": [
            {"role": "user", "content": "你是谁？"},
            {"role": "assistant", "content": "首先，我是深衡-1。其次，我由示例科技研发。"},
        ]},
        {"id": "s2", "source": "identity", "type": "sft", "messages": [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "STYLE 你好呀。"},  # 已达标，不被矫正
        ]},
    ]


def test_run_multiround_convergence():
    from lib.style_fix import run

    result = run(FakeClient(), _samples(), _cfg(), rounds=3, threshold=4)
    # s1 被矫正（1 轮 polish 即含 STYLE 达标）；s2 已达标不动
    assert result["stats"]["improved"] == 1
    assert len(result["samples"]) == 1
    corrected = result["samples"][0]["messages"][1]["content"]
    assert corrected.startswith("STYLE")
    # DPO 对：chosen=矫正版、rejected=原文
    assert len(result["dpo_pairs"]) == 1
    assert "首先，我是深衡-1" in result["dpo_pairs"][0]["rejected"][0]["content"]
    assert result["dpo_pairs"][0]["chosen"][0]["content"].startswith("STYLE")


def test_run_keeps_original_when_polish_scores_worse():
    """候选低于原文分时不得替换（旧实现以 0 为基准分，任何候选都会覆盖原文）。"""
    from lib.style_fix import run

    sample = {"id": "s1", "messages": [{"role": "user", "content": "你好"},
                                       {"role": "assistant", "content": "很棒的原始回答。"}]}

    class BetterClient(FakeClient):
        def chat(self, messages, **kwargs):
            content = messages[0]["content"]
            if "语言风格审校员" in content:
                text = content.split("# 待审文本:")[-1]
                return json.dumps({"adherence": 4 if "STYLE" in text else 2}, ensure_ascii=False)
            return super().chat(messages, **kwargs)

    better = run(BetterClient(), [sample], _cfg(), rounds=1, threshold=3)
    assert better["stats"]["improved"] == 1  # 原文 2 分 < 候选 4 分：正常采用
    assert better["samples"][0]["messages"][1]["content"].startswith("STYLE")

    class WorseClient(FakeClient):
        def chat(self, messages, **kwargs):
            content = messages[0]["content"]
            if "语言风格审校员" in content:
                text = content.split("# 待审文本:")[-1]
                return json.dumps({"adherence": 2 if "STYLE" in text else 3}, ensure_ascii=False)
            return super().chat(messages, **kwargs)

    worse = run(WorseClient(), [sample], _cfg(), rounds=1, threshold=4)
    assert worse["stats"]["improved"] == 0  # 候选 2 分 < 原文 3 分：不得回退
    assert worse["samples"] == []
    assert worse["dpo_pairs"] == []


def test_dpo_prompt_excludes_future_user_turns():
    """DPO prompt 只能包含被矫正消息之前的历史，不得泄漏后续用户轮次。"""
    from lib.style_fix import run

    sample = {"id": "s1", "messages": [
        {"role": "user", "content": "第一问"},
        {"role": "assistant", "content": "很棒的原始回答。"},
        {"role": "user", "content": "第二问（未来信息）"},
        {"role": "assistant", "content": "STYLE 第二答。"},
    ]}
    result = run(FakeClient(), [sample], _cfg(), rounds=3, threshold=4)
    assert len(result["dpo_pairs"]) == 1
    prompt = result["dpo_pairs"][0]["prompt"]
    assert [m["content"] for m in prompt] == ["第一问"]
    assert "STYLE" not in result["dpo_pairs"][0]["rejected"][0]["content"]


def test_run_clears_stale_reasoning_when_content_replaced():
    """分字段思考样本被矫正后不得同时保留旧思考（同一段思考出现两次）。"""
    from lib.style_fix import run

    sample = {"id": "s1", "messages": [
        {"role": "user", "content": "你好"},
        {"role": "assistant", "reasoning_content": "思考过程", "content": "很棒的原始回答。"},
    ]}
    result = run(FakeClient(), [sample], _cfg(), rounds=3, threshold=4)
    assert len(result["samples"]) == 1
    msg = result["samples"][0]["messages"][1]
    assert msg["reasoning_content"] == ""
    assert msg["content"].startswith("STYLE")
    assert msg["content"].count("思考过程") <= 1


def test_load_rules_parses_exemplars():
    cfg = _cfg()
    assert len(cfg["rules"]) >= 3
    assert len(cfg["exemplars"]) == 2
    assert "text" in cfg["exemplars"][0] and "corrected" in cfg["exemplars"][0]
