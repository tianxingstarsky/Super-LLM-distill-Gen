"""Agent 工具使用零参考训练数据提示词（Instructor + Simulator 双角色架构）。

零参考的通用形态：给定工具定义（JSON schema）+ 场景，合成多步 agent 轨迹
（thought → tool_call → observation → … → final_answer），与环境交互的反馈
由 Simulator（LLM 扮演执行器）生成，全程无需真实环境。
依据：AgentInstruct Instructor/Simulator（arXiv 2407.03502）、
      FireAct 工具轨迹 SFT（arXiv 2310.05915）、AgentTuning（arXiv 2310.12823）。
"""
from __future__ import annotations

from lib.prompts.base import PromptSpec

TASK_GEN = PromptSpec(
    id="agent.task_gen",
    version="1.0.0",
    purpose="给定工具集与场景，生成多样化、需多步工具调用的 agent 任务（零参考）",
    source="AgentInstruct Instructor 角色（arXiv 2407.03502）",
    variables=("tools_desc", "scenario", "n", "seen"),
    constraints=(
        "输出必须是合法 JSON，键：tasks（数组，恰好 {n} 条，每条含 goal）",
        "每个 goal 必须能借助给定工具完成，且至少需要 2 次工具调用",
        "任务必须在 scenario 场景内且互不相同；不得与 seen 重复",
    ),
    template="""你是 agent 任务生成专家。基于给定的工具集与场景，生成 {n} 个需要多步工具调用才能完成的任务。

# 可用工具:
{tools_desc}

# 目标场景:
{scenario}

# 已生成任务（禁止重复）:
{seen}

要求:
1. 每个任务目标明确、可借助工具完成，且至少需要 2 次工具调用（如：先搜索再抓取再分析）；
2. 任务在场景内尽量多样（不同主题、不同难度、不同工具组合）；
3. 只写任务目标，不写执行过程。

只输出 JSON（不要输出其他内容）:
{{"tasks": [{{"goal": "任务目标 1"}}, "…共 {n} 条"]}}""",
)

ACT = PromptSpec(
    id="agent.act",
    version="1.0.0",
    purpose="Agent 执行步：给定目标+历史+工具定义，输出下一步（思考+工具调用 或 最终回答）",
    source="AgentInstruct Simulator 反向角色（arXiv 2407.03502）+ FireAct（arXiv 2310.05915）",
    variables=("tools_desc", "goal", "history"),
    constraints=(
        "输出必须是合法 JSON",
        "继续执行时：键 thought/tool_call（tool_call 含 name/args，name 必须在工具集内，args 符合定义）",
        "任务已完成时：键 final_answer（基于历史观察回答，不得捏造未观察到的内容）",
        "不得同时输出 tool_call 与 final_answer",
    ),
    template="""你是 agent 执行者。根据任务目标与执行历史，决定下一步。

# 可用工具:
{tools_desc}

# 任务目标:
{goal}

# 执行历史:
{history}

决策规则:
1. 任务未完成 → 输出思考与一个工具调用（thought 与 tool_call，tool_call 含 name 和 args）；
2. 任务已完成 → 只输出 final_answer，内容必须基于历史中的观察，禁止捏造；
3. 参数必须符合工具定义；优先用最直接的路径，避免冗余调用。

只输出 JSON（不要输出其他内容）。继续执行:
{{"thought": "为什么调用这个工具", "tool_call": {{"name": "工具名", "args": {{...}}}}}}
任务完成:
{{"final_answer": "基于观察的最终回答"}}""",
)

SIMULATE = PromptSpec(
    id="agent.simulate",
    version="1.0.0",
    purpose="Simulator 角色：扮演工具执行器，为工具调用生成自洽的观察结果（成功/空/报错）",
    source="AgentInstruct Simulator 角色（arXiv 2407.03502）",
    variables=("tools_desc", "goal", "tool_name", "tool_args", "history"),
    constraints=(
        "输出必须是合法 JSON，键：observation",
        "observation 必须是该工具的真实形态输出（搜索结果/文件内容/代码输出/报错均可）",
        "内容必须与调用参数自洽；可含信息不足/报错场景，但不得凭空给出与参数无关的内容",
    ),
    template="""你是工具执行器模拟器。为下面的工具调用生成真实的执行结果。

# 可用工具:
{tools_desc}

# 任务目标:
{goal}

# 执行历史:
{history}

# 本次调用:
工具: {tool_name}
参数: {tool_args}

要求:
1. 返回该工具的真实形态输出（搜索结果摘要与链接/文件内容/代码输出/报错信息）；
2. 输出必须与参数自洽；允许报错或空结果；
3. 内容具体可信（有细节，不要泛泛而谈）。

只输出 JSON（不要输出其他内容）:
{{"observation": "工具执行结果"}}""",
)

CHECK = PromptSpec(
    id="agent.check",
    version="1.1.0",
    purpose="轨迹质检：格式合法、参数合法、最终回答有观察依据、无冗余绕弯子调用（agent 数据质量门）",
    source="本项目质量门设计（与 distill.reflector 同构）+ 效率守卫强化",
    variables=("goal", "trajectory"),
    constraints=(
        "输出必须是合法 JSON，键：valid/issues/keep",
        "工具名不在工具集内、参数不合定义、最终回答捏造未观察内容 → valid=false 且 keep=false",
        "存在冗余/重复/绕弯子调用（对任务无推进的工具调用）→ valid=false 且 keep=false",
        "只检查形式、依据与效率，不评价策略优劣之外的风格",
    ),
    notes="v1.1.0：新增效率守卫——冗余/绕弯子调用判 invalid（用户要求：长度向上赶但所有操作不绕弯子）。",
    template="""你是 agent 轨迹质检员。检查下面的执行轨迹是否合格。

# 任务目标:
{goal}

# 执行轨迹:
{trajectory}

检查项:
1. 每个工具调用格式合法（name/args 齐全、args 符合工具定义）；
2. 最终回答的每条结论都能在执行历史的观察中找到依据（无捏造）；
3. **效率守卫：任何对任务没有推进作用的冗余/重复/绕弯子调用（如重复搜索同一查询、
   明知空结果仍重试、与目标无关的调用）→ valid=false**；
4. 轨迹在合理步数内完成或合理中断。

只输出 JSON（不要输出其他内容）:
{{
  "valid": true/false,
  "issues": ["问题 1", "…"],
  "keep": true/false
}}""",
)
