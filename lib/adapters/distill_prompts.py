"""distill_prompts：聊天/工具日志蒸馏的文本化三角色提示词。

改编自 OpenCUA cot-generator（MIT）的 Reflector/Generator/Summarizer 提示词结构
（components/opencua/data/cot-generate/module/reflector.py、generator.py、evaluator.py），
差异：去掉全部截图/红圈/坐标依赖，输入为文本轨迹（lib/adapters/chatlog_to_traj.py 输出）。

正确性信号优先级（spike 约定，与提示词一致）：
  ① 运行时事实（exit_code/success 字段/断言）② 上下文用户信号（纠正/重试/追问）
  ③ LLM 语义判断兜底。
反思防呆约束（对应"将错误变成思维链去避免，只保留正确操作"）：
  - 错误只保留一句话教训，占思维链 ≤20%，不展开错误细节；
  - 最终回答只包含正确操作与正确工具调用；错误尝试只出现在思维链的"教训"里。
"""
from __future__ import annotations

# ── 角色 1：Reflector（文本版）─────────────────────────────────────────────
REFLECTOR_TEXT_PROMPT = """你是一名智能体操作质检员。给定任务目标、逐步执行历史（含工具调用与结果），逐段判断正确性。

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
}}"""

# ── 角色 2：Generator（文本版，含反思防呆约束）────────────────────────────
GENERATOR_TEXT_PROMPT = """你是训练数据撰写助手。把下面经过质检的智能体执行过程改写成一条带思维链的 SFT 样本。

# 任务:
{goal}

# 步骤与质检结论（错误步骤已标记）:
{annotated_steps}

写作规则（必须严格遵守）:
1. 思维链采用反思式：格式为"原计划 A → 发现会导致 X → 改用 B → B 成功"。
2. 错误只允许以"一句话教训"形式出现在思维链中，占思维链总长 ≤20%，禁止展开错误细节、禁止复述错误操作过程。
3. 最终回答只包含正确操作与正确工具调用；所有错误尝试一律不出现在最终回答里。
4. 语言与任务一致（中英混合时保持自然）。

输出 JSON（不要输出其他内容）:
{{
  "thinking": "反思式思维链（教训 ≤20%）",
  "final_answer": "只含正确操作的最终回答"
}}"""

# ── 角色 3：Summarizer（文本版评分）────────────────────────────────────────
SUMMARIZER_TEXT_PROMPT = """你是训练数据审校员。评估以下蒸馏样本的质量。

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
{{"correctness": n, "alignment": n, "efficiency": n, "lesson_quality": n, "keep": true/false}}"""
