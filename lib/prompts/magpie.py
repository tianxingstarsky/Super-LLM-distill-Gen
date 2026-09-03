"""Magpie 类指令生成提示词（资产化版本）。"""
from __future__ import annotations

from lib.prompts.base import PromptSpec

QUERY = PromptSpec(
    id="magpie.query",
    version="1.0.0",
    purpose="无种子指令生成（API 适配版）：扮演好奇用户生成单条高质量问题",
    source=(
        "Magpie 无种子生成思想（arXiv 2406.08464, ICLR 2025）的 chat API 适配 + "
        "UltraChat 问题多样化（arXiv 2305.14233）。注：原版 pre-query 模板注入在 "
        "chat API 不可行（spike 实测 R1），故采用角色扮演式生成。"
    ),
    variables=(),
    constraints=(
        "输出必须只有问题本身，无任何前缀/引号/解释",
        "每条问题必须具体且可回答，避免模板化问候语",
    ),
    template="""You are a curious user with broad interests. \
Generate ONE specific, high-quality question on any topic \
(science, programming, writing, daily life, business, health, etc.). \
Output ONLY the question itself, nothing else. \
Make each question different from common templates.""",
)
