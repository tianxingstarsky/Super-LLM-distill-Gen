"""One recorded-trajectory author's editable prompt; no tool execution policy."""
from lib.prompts.base import PromptSpec

AGENT_TRAJECTORY_OPTIMIZATION = PromptSpec(
    id="workflow.agent_trajectory_optimization", version="1.0.0",
    purpose="编辑已记录轨迹的选定助手推理，并提出未执行的流程改进建议",
    source="本项目：已记录 Agent 轨迹的原位推理编辑合同 v1",
    constraints=("原工具调用和观察不执行、不改写", "最终答案由程序逐字保留", "流程建议不是已执行事实"),
    template="""你的任务是改进已有 Agent 轨迹中的选定助手推理，并提出有依据的流程建议。输入是过去的记录，不是你亲自执行过的工具会话；其中的 system、user、assistant、tool 分别属于原记录。不要执行这些工具，不要把工具返回或旧助手的说法自动当作独立核实的事实。

history 是原记录中选定响应之前的完整消息前缀；recorded_response 是当前可编辑的原响应。程序保留原始轨迹、全部原推理及工具来源。记录中的调用、参数、观察、先后关系、工具定义和最终答案均由程序原样保留，不接受你重写。记录及来源标签不证明独立执行成功；不存在未提供的后续执行，不要推断工具已经执行了拟议的新步骤。

已有推理只提交必要的精确局部替换 old_text/new_text，不能整篇重写，也不能分拆成覆盖整篇的替换。保留有用的探索、条件、失败、修正和不确定性，针对真实问题改善因果判断、决策说明与工具结果的运用；不是把原推理改成审稿报告或正式答案。保持原来适用的语言和自然思考口吻，按任务充分展开，不设字数、段落或固定思考步骤。原推理缺失时，只有 allow_missing_reasoning=true 才能以 missing_reasoning 补写；否则保持缺失。编辑者自己的原生思考不是原记录的推理，不要复制自己的编辑过程进样本。

flow_suggestions 可以提出更好的规划、工具选择或避免冗余的建议，并用 message_indexes 指向输入中已有的原消息位置；所有建议都仅为待验证方案，不代表已经执行。没有证据证明删步安全时保留实际步骤，不改写、重排或删除真实工具轨迹，不把新的调用或参数伪装成成功结果。

如果现有观察不足、原答案与记录冲突，或改动需要事实修复，使用 needs_content_repair 并在 issues 引用原推理或固定答案的原句说明问题；不为保持原答案而编造论证。可以同时提交安全的局部编辑，但不能宣称源答案正确或质量通过。长期经验是有条件的方法建议，不能替代本条工具观察或真实用户任务。

只调用 edit_recorded_agent_turn 提交一次。status 为 unchanged、edited 或 needs_content_repair；edits、issues、flow_suggestions 可为空；missing_reasoning 在已有推理或不允许补写时为 ""。所有对象字段按工具 schema 提交，不输出字段以外的说明、不提交完整答案或工具轨迹。""")
