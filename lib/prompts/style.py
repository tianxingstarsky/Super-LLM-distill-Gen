"""思考风格偏好校验提示词（CoT 风格调教的质量门）。"""
from __future__ import annotations

from lib.prompts.base import PromptSpec

STYLE_CHECK = PromptSpec(
    id="cotstyle.check",
    version="1.0.0",
    purpose="判定思考链是否符合指定风格画像（风格符合度打分 + 违规点 + keep 决策）",
    source="本项目偏好调教质量门（与 judge.score 同构，独立维度）",
    variables=("style_description", "thinking", "goal"),
    constraints=(
        "输出必须是合法 JSON，键：adherence/violations/keep",
        "adherence 为 1-5 风格符合度；violations 列出具体违规点（无则空列表）",
        "只评价风格符合度，不评价答案正确性",
    ),
    template="""你是思考风格审校员。判断下面的思维链是否符合给定的风格要求。

# 任务:
{goal}

# 风格要求:
{style_description}

# 思维链:
{thinking}

评分规则:
1. adherence：1-5 风格符合度（5=完全符合，1=完全不符合）；
2. violations：逐条列出违反风格要求的具体表现（无则空列表）；
3. 只评价风格符合度，不评价答案正确性。

只输出 JSON（不要输出其他内容）:
{{
  "adherence": 1-5,
  "violations": ["违规点 1", "…"],
  "keep": true/false
}}""",
)
