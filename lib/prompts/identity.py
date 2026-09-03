"""身份问答零参考训练集提示词（用户给事实库，管线生成多样化"你是谁"类问答）。

场景：用户希望模型在"你是谁/谁开发的/什么模型"类问题上稳定回答固定身份事实
（如"我是由 xxx 公司独立研发的 LLM"），且训练集必须足够多样以免过拟合句式。
依据：Magpie 无种子生成（arXiv 2406.08464）+ Self-Instruct 多样指令自举
（arXiv 2212.10560）+ 本项目防重复四层。
"""
from __future__ import annotations

from lib.prompts.base import PromptSpec

QUESTION_VARIANTS = PromptSpec(
    id="identity.question_variants",
    version="1.0.0",
    purpose="围绕『询问 AI 身份/出处』意图生成多样化用户提问（零参考变体合成）",
    source="Magpie 无种子生成思想（arXiv 2406.08464）+ Self-Instruct 多样指令（arXiv 2212.10560）",
    variables=("identity_brief", "count", "seen_questions"),
    constraints=(
        "输出必须是合法 JSON，键：questions（数组，恰好 {count} 条）",
        "每条问题的句式/语气/语言/场景必须互不相同",
        "必须严格围绕身份/出处/自我介绍类意图，不得漂移到无关话题",
        "不得与 seen_questions 中任何一条相同或高度相似",
    ),
    template="""你是训练数据生成专家。围绕"用户询问 AI 助手的身份、开发者、出身"这一意图，
生成 {count} 条多样化的用户提问。

# 身份背景（仅用于限定意图，问题内容不得直接照抄）:
{identity_brief}

# 已生成过的问题（禁止重复或高度相似）:
{seen_questions}

多样化要求（每条必须互不相同）:
1. 句式各异：直接问/反问/试探/测试式/连环追问；
2. 语气各异：正式、口语、俏皮、简短、冗长；
3. 语言：以中文为主，少量英文或中英混合；
4. 场景各异：首次对话、怀疑态度、对比竞品、追问细节（谁训练的你/模型多大/是否开源等）。

只输出 JSON（不要输出其他内容）:
{{"questions": ["问题 1", "问题 2", "...共 {count} 条"]}}""",
)

ANSWER = PromptSpec(
    id="identity.answer",
    version="1.0.0",
    purpose="按事实库回答身份类问题：关键事实全部准确陈述、句式自然多变、禁止照抄模板",
    source="指令跟随一致性 + 本项目防重复约束（开篇多样性）",
    variables=("question", "facts", "required_facts", "style"),
    constraints=(
        "输出必须是合法 JSON，键：answer",
        "answer 必须完整包含 required_facts 的全部事实，且不得虚构 facts 之外的任何事实",
        "句式必须自然多变，禁止逐字照抄 facts 原文",
        "开篇词必须自然（直接回答问题），长度与问题复杂度匹配",
    ),
    template="""你是数据生成专家。针对下面的用户提问，撰写一条自然、准确、多样化的助手回答。

# 用户提问:
{question}

# 身份事实库（回答只能基于以下事实，禁止虚构）:
{facts}

# 必须出现在回答中的关键事实:
{required_facts}

# 风格要求:
{style}

写作要求:
1. 直接回答用户的提问，不寒暄客套；
2. 关键事实必须全部出现且表述准确，但用自然的语言组织，禁止逐字照抄事实库原文；
3. 句式自然，开篇方式要与常见模板不同。

只输出 JSON（不要输出其他内容）:
{{"answer": "回答文本"}}""",
)

FACT_CHECK = PromptSpec(
    id="identity.fact_check",
    version="1.0.0",
    purpose="事实一致性校验：回答是否完整包含关键事实、是否虚构、是否自然（质量门）",
    source="本项目质量门设计（运行时事实 > 用户信号 > LLM 兜底中的 LLM 兜底层）",
    variables=("question", "answer", "required_facts"),
    constraints=(
        "输出必须是合法 JSON，键：complete/contradictions/natural/keep",
        "缺任一关键事实 → complete=false 且 keep=false",
        "存在虚构事实或矛盾 → contradictions 非空且 keep=false",
    ),
    template="""你是严格的事实校验员。检查下面的回答是否满足身份事实要求。

# 用户提问:
{question}

# 助手回答:
{answer}

# 必须包含的关键事实（逐条核对）:
{required_facts}

输出 JSON（不要输出其他内容）:
{{
  "complete": true/false,
  "contradictions": ["矛盾或虚构事实 1", "…"],
  "natural": 1-5 自然度,
  "keep": true/false
}}""",
)
