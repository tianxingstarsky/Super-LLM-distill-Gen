"""语言风格强矫正提示词（多轮风格微调：去 AI 味，靠用户注入的规则与示例）。"""
from __future__ import annotations

from lib.prompts.base import PromptSpec

POLISH = PromptSpec(
    id="stylefix.polish",
    version="1.0.0",
    purpose="按用户规则+示例重写文本：消除 AI 味（模板词/套话/机械排比），保留原意与事实",
    source="风格条件化生成 + Self-Refine 多轮精炼（arXiv 2303.17651）组合",
    variables=("text", "rules", "exemplars"),
    constraints=(
        "输出必须是合法 JSON，键：corrected/changes",
        "corrected 必须保留原意的全部事实与信息，只改语言风格",
        "changes 列出具体修改点（≥1 条）",
        "示例对优先于抽象规则（风格化更明显）",
    ),
    template="""你是语言风格矫正专家。把下面的文本重写成"更像真人写"的风格。

# 待矫正文本:
{text}

# 用户风格规则（必须遵守）:
{rules}

# 用户示例（优先模仿；示例的写作方式比抽象规则更具体）:
{exemplars}

矫正要求:
1. 消除 AI 味：删去"首先/其次/综上所述/值得注意的是"等模板词、空洞套话、机械排比与过度结构化；
2. 保留原文全部事实与信息，只改语言；
3. 让句子像真人自然说话/写作，可以打破原来整齐的段落结构；
4. 示例对优先于抽象规则——用户给了示例就照着示例的味道写。

只输出 JSON（不要输出其他内容）:
{{"corrected": "矫正后的文本", "changes": ["修改点 1", "…"]}}""",
)

CHECK = PromptSpec(
    id="stylefix.check",
    version="1.0.0",
    purpose="风格符合度判定：文本是否达到用户规则/示例要求（多轮矫正的收敛判据）",
    source="本项目质量门设计（与 cotstyle.check 同构，通用文本版）",
    variables=("text", "rules"),
    constraints=(
        "输出必须是合法 JSON，键：adherence/violations/keep",
        "adherence 为 1-5 风格符合度；violations 列出残留的 AI 味表现",
        "只评价风格，不评价内容正确性",
    ),
    template="""你是语言风格审校员。判断下面的文本是否符合用户的风格要求。

# 风格要求:
{rules}

# 待审文本:
{text}

评分规则:
1. adherence：1-5 风格符合度（5=完全像真人写作，1=AI 味很重）；
2. violations：列出残留的 AI 味表现（模板词/套话/机械结构等，无则空列表）；
3. 只评价风格，不评价内容正确性。

只输出 JSON（不要输出其他内容）:
{{
  "adherence": 1-5,
  "violations": ["残留 AI 味 1", "…"],
  "keep": true/false
}}""",
)
