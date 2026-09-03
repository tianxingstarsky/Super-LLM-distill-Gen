"""蒸馏三角色提示词（资产化版本）。

改编自 OpenCUA cot-generator（MIT，components/opencua/data/cot-generate/module/）
的 Reflector/Generator/Summarizer 结构，去除全部截图/坐标依赖，输入为文本轨迹。
正确性信号优先级：① 运行时事实（exit_code/success/isError）② 用户信号（纠正/重试/追问）
③ LLM 语义判断兜底。
"""
from __future__ import annotations

from lib.prompts.base import PromptSpec

REFLECTOR = PromptSpec(
    id="distill.reflector",
    version="1.0.0",
    purpose="逐段质检：判定步骤冗余/错误并产出一句话教训（正确性信号按优先级分层）",
    source="OpenCUA cot-generator Reflector 文本化改编（xlang-ai/OpenCUA, MIT, arXiv 2508.09123）",
    variables=("goal", "history_steps", "last_step"),
    constraints=(
        "输出必须是合法 JSON，键：redundant/incorrect/error_type/lesson",
        "正确性判定优先级：运行时事实 > 用户信号 > 语义判断",
        "lesson 只允许一句话（≤40 字），无错误时为空字符串",
    ),
    template="""你是一名智能体操作质检员。给定任务目标、逐步执行历史（含工具调用与结果），逐段判断正确性。

# 任务:
{goal}

# 步骤历史:
{history_steps}

# 最后一步:
{last_step}

判断依据（优先级从高到低）:
1. 运行时事实：exit_code 非 0、success=false、断言失败、报错输出 —— 直接判为错误；
2. 用户信号：用户下一步的纠正、重试、追问 —— 该步判为错误/不完整；
3. 语义判断：与任务目标无关、重复前一步、明显冗余 —— 判为冗余或错误。

输出 JSON（不要输出其他内容）:
{{
  "redundant": true/false,
  "incorrect": true/false,
  "error_type": "运行时错误|用户纠正|冗余|无",
  "lesson": "一句话教训（≤40 字），无错误时填空字符串"
}}""",
)

GENERATOR = PromptSpec(
    id="distill.generator",
    version="1.1.0",
    purpose="把经质检的执行过程改写成反思式思维链 SFT 样本（错误只进教训、最终只留正确操作）",
    source="OpenCUA cot-generator Generator 文本化改编（xlang-ai/OpenCUA, MIT, arXiv 2508.09123）",
    variables=("goal", "annotated_steps"),
    constraints=(
        "只输出一个 JSON 对象（以 { 开头、以 } 结尾），禁止任何其他文本、Markdown 围栏或前缀",
        "JSON 键：thinking/final_answer",
        "错误只允许以一句话教训形式出现在 thinking 中，占比 ≤20%，禁止展开错误细节",
        "final_answer 只包含正确操作与正确工具调用",
        "思维链格式：原计划 A → 发现会导致 X → 改用 B → B 成功",
    ),
    notes=(
        "v1.1.0：真机评测（df prompt-eval）发现 v4-pro 在 v1.0.0 下偶发输出 '-zh:/final-answer:' "
        "乱格式而非 JSON；补强 JSON 唯一输出死命令。"
    ),
    template="""你是训练数据撰写助手。把下面经过质检的智能体执行过程改写成一条带思维链的 SFT 样本。

# 任务:
{goal}

# 步骤与质检结论（错误步骤已标记）:
{annotated_steps}

写作规则（必须严格遵守）:
1. 思维链采用反思式：格式为"原计划 A → 发现会导致 X → 改用 B → B 成功"。
2. 错误只允许以"一句话教训"形式出现在思维链中，占思维链总长 ≤20%，禁止展开错误细节、禁止复述错误操作过程。
3. 最终回答只包含正确操作与正确工具调用；所有错误尝试一律不出现在最终回答里。
4. 语言与任务一致（中英混合时保持自然）。

输出要求（最重要）：只输出一个 JSON 对象，以 {{ 开头、以 }} 结尾，不得输出任何其他文本、
解释、Markdown 代码块围栏或前缀。JSON 键必须恰好是 thinking 和 final_answer：

{{"thinking": "反思式思维链（教训 ≤20%）", "final_answer": "只含正确操作的最终回答"}}""",
)

SUMMARIZER = PromptSpec(
    id="distill.summarizer",
    version="1.0.0",
    purpose="五维质量打分并给出 keep 决策（judge 角色同用此提示词）",
    source="OpenCUA cot-generator Summarizer 文本化改编 + UltraFeedback 维度思想（arXiv 2310.01377）",
    variables=("goal", "thinking", "final_answer"),
    constraints=(
        "输出必须是合法 JSON，键：correctness/alignment/efficiency/lesson_quality/keep（各 1-5 分，keep 布尔）",
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
