"""翻译与知识桥接提示词（M2 新管线，资产化版本）。

依据：Bactrian-X（arXiv 2305.15011，跨语言指令数据的翻译扩展方法）、
      SeaLLMs（arXiv 2312.00738，用英语知识富集目标语言的"知识桥接"）、
      MADLAD-400（arXiv 2309.04662，翻译语料标准）。
"""
from __future__ import annotations

from lib.prompts.base import PromptSpec

ZH2EN = PromptSpec(
    id="translation.zh2en",
    version="1.0.0",
    purpose="中文 → 英文翻译（保留专业术语准确性，附术语对照）",
    source="Bactrian-X 翻译扩展方法（arXiv 2305.15011）；WMT 术语保留惯例",
    variables=("text",),
    constraints=(
        "输出必须是合法 JSON，键：translation/terms",
        "translation 为纯译文；terms 为专业术语中英对照列表（无术语时空列表）",
    ),
    template="""你是专业中英译者。将下面的中文翻译成地道的英文，专业术语必须准确。

# 原文:
{text}

输出 JSON（不要输出其他内容）:
{{
  "translation": "英文译文",
  "terms": [["中文术语", "English term"]]
}}""",
)

EN2ZH = PromptSpec(
    id="translation.en2zh",
    version="1.0.0",
    purpose="英文 → 中文翻译（忠实通顺，关键术语附解释）",
    source="Bactrian-X 翻译扩展方法（arXiv 2305.15011）",
    variables=("text",),
    constraints=(
        "输出必须是合法 JSON，键：translation/terms",
        "translation 为纯译文；terms 为关键术语解释列表（无术语时空列表）",
    ),
    template="""你是专业英中译者。将下面的英文翻译成忠实、通顺的中文。

# 原文:
{text}

输出 JSON（不要输出其他内容）:
{{
  "translation": "中文译文",
  "terms": [["English term", "中文解释"]]
}}""",
)

BRIDGE_ZH2EN = PromptSpec(
    id="translation.bridge_zh2en",
    version="1.0.0",
    purpose="知识桥接：中文知识用英文完整表述（强化中英文知识关联）",
    source="SeaLLMs 知识桥接方法（arXiv 2312.00738）",
    variables=("question",),
    constraints=(
        "输出必须是合法 JSON，键：english/zh_summary",
        "english 为完整英文作答；zh_summary 为中文要点（≤3 条）",
    ),
    template="""你是中英双语专家。先用英文完整回答下面的中文问题，再给出中文要点，
以此在两种语言间建立知识关联。

# 问题:
{question}

输出 JSON（不要输出其他内容）:
{{
  "english": "完整英文回答",
  "zh_summary": "中文要点 1；要点 2；要点 3"
}}""",
)

BRIDGE_EN2ZH = PromptSpec(
    id="translation.bridge_en2zh",
    version="1.0.0",
    purpose="知识桥接：英文知识用中文完整表述（强化中英文知识关联）",
    source="SeaLLMs 知识桥接方法（arXiv 2312.00738）",
    variables=("question",),
    constraints=(
        "输出必须是合法 JSON，键：chinese/en_summary",
        "chinese 为完整中文作答；en_summary 为英文要点（≤3 条）",
    ),
    template="""You are a bilingual expert. Answer the following English question fully in Chinese first, \
then provide key points in English, to build knowledge connections across the two languages.

# Question:
{question}

Output JSON only:
{{
  "chinese": "完整中文回答",
  "en_summary": "point 1; point 2; point 3"
}}""",
)

BACKCHECK = PromptSpec(
    id="translation.backcheck",
    version="1.0.0",
    purpose="回译校验：判断回译是否忠实于原文（翻译管线质量门）",
    source="回译校验惯例（Bactrian-X/MADLAD-400 数据管线中的质量检查）",
    variables=("original", "back_translation"),
    constraints=(
        "输出必须是合法 JSON，键：faithful/score/issues",
        "score 为 1-5 忠实度；issues 为问题列表（无问题时空列表）",
    ),
    template="""你是翻译质检员。判断回译结果是否忠实于原文（信息是否丢失/扭曲/增删）。

# 原文:
{original}

# 回译:
{back_translation}

输出 JSON（不要输出其他内容）:
{{
  "faithful": true/false,
  "score": 1-5 忠实度,
  "issues": ["问题 1", "问题 2"]
}}""",
)
