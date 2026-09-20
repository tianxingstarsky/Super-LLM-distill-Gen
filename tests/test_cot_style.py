"""CoT 风格调教离线测试（fake client 脚本化：生成→风格校验→SFT/DPO 决策）。"""
from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


class FakeClient:
    """生成：带风格版 adherence 高；无风格版低。校验：按 thinking 是否含 'STYLE' 判分。"""

    def __init__(self):
        self.usage = {"calls": 0}

    def chat(self, messages, **kwargs):
        self.usage["calls"] += 1
        content = messages[0]["content"]
        if "思考风格审校员" in content:  # cotstyle.check 唯一标记（优先判定）
            thinking = content.split("# 思维链:")[-1]
            high = "STYLE" in thinking
            return json.dumps({
                "adherence": 5 if high else 1,
                "violations": [] if high else ["未遵守风格"],
                "keep": high,
            }, ensure_ascii=False)
        # distill.generator：出现具体风格描述词（非"默认风格"块）视为带风格
        styled = any(d in content for d in ["思考极简", "编号要点", "可能的坑", "思考过程使用中文"])
        return json.dumps({
            "thinking": ("STYLE|" if styled else "PLAIN|") + "原计划A→发现X→改用B",
            "final_answer": "正确答案",
        }, ensure_ascii=False)


def _styles():
    from lib.cot_style import load_styles

    return load_styles(ROOT / "configs" / "cot_styles.yaml")


def test_load_styles_and_style_block():
    from lib.cot_style import DEFAULT_STYLE, load_styles, style_block

    cfg = load_styles(ROOT / "configs" / "cot_styles.yaml")
    assert "concise" in cfg["styles"] and "chinese" in cfg["styles"]
    assert style_block(None) == DEFAULT_STYLE
    assert "风格：" in style_block("思考极简")


def test_run_produces_styled_samples_and_dpo(tmp_path):
    from lib.cot_style import run

    tasks = [
        {"goal": "任务一", "annotated_steps": "- a [正确]"},
        {"goal": "任务二", "annotated_steps": "- b [错误]\n- c [正确]"},
    ]
    result = run(FakeClient(), tasks, _styles())
    # 带风格版 adherence=5 ≥4 → 全部保留为 styled SFT
    assert result["stats"]["kept_styled"] == 2
    # 两版 adherence 差 = 4 ≥2 → 每个任务一对风格 DPO
    assert result["stats"]["dpo_pairs"] == 2
    for s in result["samples"]:
        assert s["source"] == "cotstyle" and s["style"] in _styles()["styles"]
        assert s["messages"][1]["reasoning_content"].startswith("STYLE|")
    for pair in result["dpo_pairs"]:
        assert pair["chosen"][0]["reasoning_content"].startswith("STYLE|")
        assert pair["rejected"][0]["reasoning_content"].startswith("PLAIN|")


def test_baseline_wins_not_labeled_as_styled():
    """无风格版反而更贴合时：不入 styled 样本库、单独计数，DPO 照常保留偏好事实。"""

    class InvertedClient(FakeClient):
        def chat(self, messages, **kwargs):
            content = messages[0]["content"]
            if "思考风格审校员" in content:
                thinking = content.split("# 思维链:")[-1]
                high = "PLAIN" in thinking  # 反转：无风格版判高分
                return json.dumps({
                    "adherence": 5 if high else 1,
                    "violations": [] if high else ["未遵守风格"],
                    "keep": high,
                }, ensure_ascii=False)
            return super().chat(messages, **kwargs)

    from lib.cot_style import run

    result = run(InvertedClient(), [{"goal": "任务一", "annotated_steps": "- a [正确]"}], _styles())
    assert result["samples"] == []  # 无风格内容不得打着风格标签入库
    assert result["stats"]["baseline_wins"] == 1
    assert result["stats"]["kept_styled"] == 0
    # 偏好事实保留：chosen=高分版（PLAIN）、rejected=低分版（STYLE）
    assert result["stats"]["dpo_pairs"] == 1
    assert result["dpo_pairs"][0]["chosen"][0]["reasoning_content"].startswith("PLAIN|")
    assert result["dpo_pairs"][0]["rejected"][0]["reasoning_content"].startswith("STYLE|")
