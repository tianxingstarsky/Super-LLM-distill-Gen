"""文档问答生成提示词（表达层：CPT 之后的 SFT 数据，教"把知识讲出来"）。

定位（两段式配方的第二段）：知识注入走 doc2corpus（CPT 语料，完整无损）；
本模块生成基于文段的问答/指令 SFT 数据，核心质量门是事实依据校验（防幻觉）。
依据：open-agentinstruct 内容转换→指令生成（arXiv 2407.03502）、
      Self-Instruct 问答自举（arXiv 2212.10560）。
"""
from __future__ import annotations

from lib.prompts.base import PromptSpec

QA_GEN = PromptSpec(
    id="document.qa_gen",
    version="1.0.0",
    purpose="基于文档片段生成多样化问答对（答案必须完全基于文段事实）",
    source="open-agentinstruct 内容转换（arXiv 2407.03502）+ Self-Instruct（arXiv 2212.10560）",
    variables=("chunk", "n"),
    constraints=(
        "输出必须是合法 JSON，键：qa（数组，恰好 {n} 条，每条含 question/answer）",
        "answer 必须完全基于文段中的事实，禁止引入文段之外的知识或编造",
        "问题类型必须多样：事实细节/定义解释/对比归纳/推理判断/应用场景",
        "问题与答案语言与文段保持一致",
    ),
    template="""你是数据生成专家。基于下面的文档片段，生成 {n} 条问答对，用于训练模型掌握这段知识。

# 文档片段:
{chunk}

生成要求:
1. 答案必须完全基于文段中的事实，禁止引入文段之外的知识，禁止编造；
2. 问题类型尽量多样：事实细节、定义解释、对比归纳、推理判断、应用场景各来一些；
3. 问题具体、可回答，答案完整且表述自然（不要照抄文段原句）；
4. 语言与文段保持一致。

只输出 JSON（不要输出其他内容）:
{{"qa": [{{"question": "问题 1", "answer": "答案 1"}}, "...共 {n} 条"]}}""",
)

INSTRUCTION_GEN = PromptSpec(
    id="document.instruction_gen",
    version="1.0.0",
    purpose="基于文档片段生成可执行任务指令（摘要/改写/提取/整理类，供指令跟随 SFT 用）",
    source="open-agentinstruct 指令生成阶段（arXiv 2407.03502）",
    variables=("chunk",),
    constraints=(
        "输出必须是合法 JSON，键：instructions（数组）",
        "每条指令必须是仅凭文段即可完成的任务描述（摘要/改写/提取/整理/润色等）",
        "指令之间任务类型不得重复",
    ),
    template="""你是数据生成专家。基于下面的文档片段，生成若干条"可仅凭该片段完成"的任务指令，
用于训练模型的指令跟随能力。

# 文档片段:
{chunk}

生成要求:
1. 每条指令是一个任务描述（如"把这段内容压缩成 50 字摘要""提取其中的所有数值并列表"）；
2. 任务类型互不重复：摘要、改写、提取、整理、润色、转述等各来一些；
3. 指令清晰可执行，不依赖文段之外的信息。

只输出 JSON（不要输出其他内容）:
{{"instructions": ["指令 1", "指令 2", "..."]}}""",
)

CROSS_CHUNK_QA = PromptSpec(
    id="document.cross_chunk_qa",
    version="1.0.0",
    purpose="跨块综合分析问答：需要通读多块才能回答的对比/归纳/因果/综述问题（知识学习自然长数据）",
    source="open-agentinstruct 内容转换（arXiv 2407.03502）+ 长文档 QA 惯例",
    variables=("chunks", "n"),
    constraints=(
        "输出必须是合法 JSON，键：qa（数组，恰好 {n} 条，每条含 question/answer）",
        "问题必须要求整合 chunks 中多处信息才能完整回答（对比/归纳/因果/综述类），单块可答的问题不合格",
        "答案必须完全基于 chunks 中的事实，禁止编造；答案按需充分展开",
    ),
    template="""你是数据生成专家。基于下面的多段文档内容（来自同一份文档的不同部分），
生成 {n} 条必须通读全部段落才能完整回答的综合分析问答。

# 文档多段内容:
{chunks}

生成要求:
1. 问题类型：跨段对比、整体归纳、因果关系、综合综述——任何一段单独都无法完整回答；
2. 答案必须完全基于文段事实，禁止编造；答案按问题需要充分展开（这是自然的长回答）；
3. 问题像真实读者通读全文后会问的样子。

只输出 JSON（不要输出其他内容）:
{{"qa": [{{"question": "综合分析问题 1", "answer": "综合答案 1"}}, "…共 {n} 条"]}}""",
)

GROUND_CHECK = PromptSpec(
    id="document.ground_check",
    version="1.0.0",
    purpose="事实依据校验（质量门）：回答中每条陈述必须能在文段中找到依据，防止幻觉数据进入训练集",
    source="本项目质量门设计（doc2data 防幻觉关键闸门）",
    variables=("chunk", "question", "answer"),
    constraints=(
        "输出必须是合法 JSON，键：grounded/unsupported/keep",
        "answer 中存在文段之外的任何事实或数字 → unsupported 列出，grounded=false 且 keep=false",
        "只检查事实依据，不评价文笔",
    ),
    template="""你是严格的事实校验员。逐条检查回答中的事实陈述是否都能在文段中找到依据。

# 文档片段:
{chunk}

# 问题:
{question}

# 回答:
{answer}

校验规则:
1. 回答中每条事实（数字、名称、时间、因果、属性）都必须在文段中有明确依据；
2. 文段中没有的信息（哪怕常识正确）也算无依据，列入 unsupported；
3. 只检查事实依据，不评价文笔。

只输出 JSON（不要输出其他内容）:
{{
  "grounded": true/false,
  "unsupported": ["无依据的陈述 1", "…"],
  "keep": true/false
}}""",
)
