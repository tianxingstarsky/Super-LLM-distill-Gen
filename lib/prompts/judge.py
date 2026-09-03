"""judge 提示词（资产化版本）。"""
from __future__ import annotations

from lib.prompts.base import PromptSpec

SCORE = PromptSpec(
    id="judge.score",
    version="1.0.0",
    purpose="样本质量五维打分与 keep 决策（与 distill.summarizer 同构，独立注册便于单独迭代）",
    source="UltraFeedback 四维判分思想（arXiv 2310.01377）+ 本项目 lesson_quality 扩展",
    variables=("goal", "thinking", "final_answer"),
    constraints=(
        "输出必须是合法 JSON，键：correctness/alignment/efficiency/lesson_quality/keep",
        "final_answer 含错误操作时 correctness 必须为 1 且 keep=false",
    ),
    template="""你是训练数据审校员。评估以下蒸馏样本的质量。

# 任务:
{goal}

# 思维链:
{thinking}

# 最终回答:
{final_answer}

评分维度（各 1-5 分）:
- correctness: 最终回答是否只含正确操作（含错误操作直接 1 分）
- alignment: 回答是否完成原始任务
- efficiency: 思维链是否简洁、无冗余复述
- lesson_quality: 教训是否一句话点到为止（冗长复述错误扣分）

输出 JSON（不要输出其他内容）:
{{"correctness": n, "alignment": n, "efficiency": n, "lesson_quality": n, "keep": true/false}}""",
)
