"""多模态图文训练数据提示词（VL 视觉引擎驱动）。

流程：vision.caption（图片→结构化描述）→ vision.qa_gen / vision.chat_gen
（基于描述生成问答/多轮对话）→ vision.consistency（VL 一致性校验，防幻觉质量门）。
依据：ShareGPT4V captioner→指令合成（arXiv 2311.12793）、LLaVA-DPO 幻觉抑制思路
（arXiv 2309.14525）、Cambrian-1 数据引擎配比（arXiv 2406.16860）。
"""
from __future__ import annotations

from lib.prompts.base import PromptSpec

CAPTION = PromptSpec(
    id="vision.caption",
    version="1.0.0",
    purpose="图片→结构化详细描述（后续问答/对话生成的种子，ShareGPT4V captioner 角色）",
    source="ShareGPT4V captioner（arXiv 2311.12793, ECCV 2024）",
    variables=(),
    constraints=(
        "输出必须是合法 JSON，键：caption/objects/text_content/scene",
        "caption 为详细中文描述（≥100 字）：主体、动作、布局、颜色、上下文",
        "objects 列出主要物体；text_content 仅抄录图片中真实可见的文本（无文本则空）",
        "禁止描述图片中不存在的内容",
    ),
    template="""你是视觉理解专家。请仔细查看这张图片，并输出结构化描述。

输出 JSON（不要输出其他内容）:
{{
  "caption": "详细中文描述（≥100字：主体、动作、空间布局、颜色、背景与氛围）",
  "objects": ["主要物体 1", "主要物体 2", "…"],
  "text_content": "图片中真实可见的文字（逐字抄录；没有则空字符串）",
  "scene": "场景类型（如：室内/户外/图表/文档截图/人像…）"
}}""",
)

QA_GEN = PromptSpec(
    id="vision.qa_gen",
    version="1.0.0",
    purpose="基于图片描述生成多样化问答（识别/空间关系/文本引用/推理/场景判断）",
    source="ShareGPT4V 指令合成 + Cambrian-1 问答配方（arXiv 2406.16860）",
    variables=("caption", "n"),
    constraints=(
        "输出必须是合法 JSON，键：qa（数组，恰好 {n} 条，每条含 question/answer）",
        "问答必须完全基于图片描述，禁止编造图片中不存在的物体/文本/关系",
        "问题类型必须多样：识别、空间关系、文本引用、推理判断、场景描述各来一些",
    ),
    template="""你是多模态数据生成专家。基于下面的图片描述，生成 {n} 条关于这张图片的问答对。

# 图片描述:
{caption}

生成要求:
1. 答案必须完全基于图片描述中的事实，禁止编造图中不存在的内容；
2. 问题类型尽量多样：物体识别、空间位置关系、图中文本引用、推理判断、场景描述；
3. 问题具体、像真实用户会问的样子，答案自然完整。

只输出 JSON（不要输出其他内容）:
{{"qa": [{{"question": "问题 1", "answer": "答案 1"}}, "…共 {n} 条"]}}""",
)

CHAT_GEN = PromptSpec(
    id="vision.chat_gen",
    version="1.0.0",
    purpose="基于图片描述生成 3 轮图文多轮对话（识别→追问细节→推理应用）",
    source="ShareGPT4V 多轮对话合成思想（arXiv 2311.12793）",
    variables=("caption",),
    constraints=(
        "输出必须是合法 JSON，键：turns（数组，恰好 3 条，每条含 user/assistant）",
        "第 1 轮识别类提问、第 2 轮追问细节、第 3 轮推理或应用",
        "全部回答必须基于图片描述，禁止编造",
    ),
    template="""你是多模态数据生成专家。基于下面的图片描述，生成一段关于这张图片的 3 轮对话。

# 图片描述:
{caption}

生成要求:
1. 第 1 轮：用户提问图片整体内容（识别类）；助手作答；
2. 第 2 轮：用户追问某个细节（位置/文字/颜色等）；助手结合前文作答；
3. 第 3 轮：用户提出推理或应用类问题；助手给出有理有据的回答；
4. 全部回答基于图片描述，禁止编造；语言自然。

只输出 JSON（不要输出其他内容）:
{{"turns": [{{"user": "第1轮提问", "assistant": "第1轮回答"}}, "…共 3 轮"]}}""",
)

CONSISTENCY = PromptSpec(
    id="vision.consistency",
    version="1.0.0",
    purpose="VL 一致性校验：回答是否与图片实际内容一致（多模态防幻觉质量门）",
    source="LLaVA-DPO 幻觉抑制思路（arXiv 2309.14525）+ 本项目质量门设计",
    variables=("question", "answer"),
    constraints=(
        "输出必须是合法 JSON，键：consistent/hallucinated/keep",
        "答案中出现图片里不存在的内容 → hallucinated 逐条列出，consistent=false 且 keep=false",
        "只检查与图片的事实一致性，不评价文笔",
    ),
    template="""你是严格的多模态事实校验员。请对照图片，检查回答是否与图片实际内容一致。

# 用户问题:
{question}

# 待校验回答:
{answer}

校验规则:
1. 回答中每条关于图片的陈述（物体、数量、颜色、位置、文字）都必须与图片实际内容一致；
2. 图片中不存在的内容（哪怕常识正确）→ 列入 hallucinated；
3. 只检查事实一致性，不评价文笔。

只输出 JSON（不要输出其他内容）:
{{
  "consistent": true/false,
  "hallucinated": ["与图片不符的陈述 1", "…"],
  "keep": true/false
}}""",
)
