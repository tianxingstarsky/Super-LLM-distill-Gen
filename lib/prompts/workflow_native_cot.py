# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Modified 2026-10-27: EVOLVE_EXPERIENCE_BODY adapts ReasoningBank's
# successful/failed/parallel memory extraction prompts into conditional,
# evidence-linked user style proposals. The native response guidance is
# this project's own asset. Upstream commit and notices: docs/licenses/README.md.
"""Native-response guidance; the experience body is re-exported for compatibility."""
from lib.prompts.workflow_experience import EVOLVE_EXPERIENCE_BODY
from lib.prompts.workflow_spec import spec
from dataclasses import replace as _reasoning_spec_replace


DRAFT_CLAIM_CALIBRATION = """在草稿中辨清准备输出的判断究竟有事实支持、依赖条件，还是仅为可尝试的建议。定稿前核对会改变结论的条件和措辞：局部观察不扩大成普遍规律，论文采用的方法不说成唯一可行的方法，经验建议不包装成有依据的阈值，尚待验证的作用不说成效果保证。发现草稿说过头时，在这次思考中修正判断，并把对应条件带进最终答案；不能只加“可能”而保留错误关系。有充分依据的结论应直接说明，不机械削弱所有表述。"""

NATIVE_COT_BODY = """解决真实对话中用户当前的问题。先在原生思考通道中推敲，再在正文通道直接回答。
从影响判断的用户原话和疑点出发，区分用户已确认的事实、希望得到的结论、历史助手的断言与可核对的知识。历史助手和待修订答复都可能有错，不能因为它们写得肯定就继承其结论，也不能倒推理由来维护旧答案。
思考以回答者的“我”为视角，用户的操作属于用户；工具结果属于工具。除准确引语外不在思考中向“你”交付答案。让草稿、必要的推导、条件核对和实际发现的修正随问题自然展开；不要先写完整答案再补理由，不表演犹豫、错误或顿悟，不套固定步骤、标题、句式或长度。
按实际疑点核对支持关系：分母、单位、比较对象和条件是否一致；能做某种比较不等于已经观察到提升，设计意图不等于效果保证。缺少的事实保持未知，不能替错误推断补条件。某个具体流程采用一种方法不证明其他方法都不可能。必要条件要落在最终结论中，不能只在思考里指出。
知识应用直接运用知识解决问题，可自然引用有依据的真实作品名，不把后台资料包装当成用户发言，不编造个人经历。用户限定选段时，只对选段所给事实作判断：缺失的表格、脚注指向或全文细节不能靠记忆补入。
把有用的自查融入这一次思考。修订时允许同时改变旧推理和旧答案，以真实问题和依据为准。输出全部原生思考及最终答复，不另写一份模拟思考，不输出字段、JSON、检查表或评审通过声明，不复述本段执行要求。最终答复使用用户要求的语言。
"""

CONCISE_COMPREHENSIVE_REASONING = """默认风格是短、简洁、全面：保留足以支撑当前结论的关键依据，以及确实影响判断的反证、适用条件和实际修正。压缩重复探索、无贡献的试探、同义复述和来回重写；不要删除必要推导或用一句结论代替完整作答。围绕实际疑点推敲并据此修正，不反复起草多份完整答案，不为了展示反思故意走错或表演自问自答。支持、反证和反思按任务需要自然出现；没有相关反证或真实修正时不编造，不要求每题都有正反两面、编号步骤或统一栏目。简单问题直接解决；复杂问题保留会改变答案的关键关系。用户明确指定的篇幅、深度和风格仍须遵守。只用这一次原生思考与同次答案完成任务，不要求另写模拟思考或另开评审调用。"""

COT_NATIVE_JOINT = spec("cot_native_joint", "引导原生思考并同次生成答案", NATIVE_COT_BODY,
    source="DeepSeek Thinking Mode: https://api-docs.deepseek.com/guides/thinking_mode/")

COT_NATIVE_JOINT_V1_0 = COT_NATIVE_JOINT

COT_NATIVE_JOINT = _reasoning_spec_replace(COT_NATIVE_JOINT, version="1.1.0",
    constraints=("保留原生思考与答案", "草稿与定稿的条件和断言一致", "不能编造来源和工具结果"),
    template=COT_NATIVE_JOINT.template + "\n" + DRAFT_CLAIM_CALIBRATION)

COT_NATIVE_JOINT_V1_1 = COT_NATIVE_JOINT

COT_NATIVE_JOINT = _reasoning_spec_replace(COT_NATIVE_JOINT, version="1.2.0",
    constraints=COT_NATIVE_JOINT.constraints + ("简洁保留必要依据与真实修正",),
    template=COT_NATIVE_JOINT.template + "\n" + CONCISE_COMPREHENSIVE_REASONING)
