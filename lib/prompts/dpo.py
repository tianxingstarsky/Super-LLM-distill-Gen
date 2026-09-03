"""DPO 偏好对增强提示词。

依据：Self-Refine 自反馈精炼（arXiv 2303.17651）、UltraFeedback 多候选判分
（arXiv 2310.01377）、LLaVA-DPO 幻觉负样本思路（arXiv 2309.14525，文本版）。
"""
from __future__ import annotations

from lib.prompts.base import PromptSpec

REFINE = PromptSpec(
    id="dpo.refine",
    version="1.0.0",
    purpose="自反馈精炼：先给当前回答挑毛病，再产出改进版（原版=rejected、精炼版=chosen）",
    source="Self-Refine（arXiv 2303.17651, NeurIPS 2023）",
    variables=("prompt", "answer"),
    constraints=(
        "输出必须是合法 JSON，键：critique/refined",
        "critique 必须指出当前回答的具体缺陷（≥1 条）",
        "refined 必须是实质改进版（保留正确内容、修正缺陷、不引入新错误）",
    ),
    template="""你是回答精炼专家。先挑出当前回答的毛病，再写一个实质改进版。

# 用户问题:
{prompt}

# 当前回答:
{answer}

输出 JSON（不要输出其他内容）:
{{
  "critique": "当前回答的具体缺陷（1-3 条）",
  "refined": "实质改进后的回答"
}}""",
)

HALLUCINATE = PromptSpec(
    id="dpo.hallucinate",
    version="1.0.0",
    purpose="生成『看似合理但包含事实错误』的负样本回答（正确回答为 chosen，本输出为 rejected）",
    source="LLaVA-DPO 幻觉负样本思路（arXiv 2309.14525）文本适配",
    variables=("prompt", "facts"),
    constraints=(
        "输出必须是合法 JSON，键：answer",
        "answer 必须与 facts 存在 1-2 处不易察觉的事实错误（数字/名称/因果），其余部分貌似合理",
        "不得直接照抄 facts，也不得写明显离谱的胡话",
    ),
    template="""你是偏好数据生成专家。为下面的问题写一个"看似合理但含事实错误"的回答，
用于训练模型学会区分正确与错误回答。

# 用户问题:
{prompt}

# 正确事实依据（生成时故意歪曲其中 1-2 处）:
{facts}

要求:
1. 回答整体流畅、貌似专业，但其中 1-2 处事实（数字/名称/因果）与事实依据不符；
2. 不要照抄事实依据原文，也不要写得明显离谱。

只输出 JSON（不要输出其他内容）:
{{"answer": "含隐蔽事实错误的回答"}}""",
)
