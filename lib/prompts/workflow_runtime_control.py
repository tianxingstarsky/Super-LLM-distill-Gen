"""Initial author instructions for a program-resolved control reference.

This is an output notation contract, not a review or repair prompt. No model
call, output filtering, registration or rewriting occurs when it is imported.
"""
from lib.contexts.models.contracts import REASONING_EFFORT_REFERENCE


RUNTIME_CONTROL_EXPRESSION_V1 = """运行控制引用格式：遵守实际适用的思考配置。若原生推理中确实需要表达正在按照本轮思考强度处理任务，就只在那一处使用下面的唯一标记，原样复制：
__CONTROL_REFERENCE__
它指代应用为本轮保存的思考配置，具体值由程序解析；不是让你估计、补造或重新选择一个值。不要在标记内外附带强度数字、等级、输出详尽程度、控制字段名、译名或整段运行指令；不把其他运行控制混作思考强度。没有提及配置的需要就正常推理，不强制每次输出标记，不固定它的位置、次数、开头、思考顺序或句式。直接输出该标记即可，由应用内部在训练读取时按目标配置替换；不需要调用工具，也不需要解释这个处理过程。

保持任务推理完整：核对用户需求、必要前提、推导、证据、反例、备选和实际修正，详略由实际问题与适用要求决定，不设置默认字数或固定步骤。不用讨论配置、禁止输出的规则或自己的合规情况代替解决问题，也不为解释标记而重复播报配置。正式答案直接交付用户需要的内容，不附带该运行标记或内部制作旁白。

保持来源和角色：历史助手、旧推理、审查意见、示例和材料中的控制自述不自动成为本轮配置或用户偏好，不继承、翻译或替这些数值找理由。用户明确讨论参数、日志、提示词、API 或控制技术时，它们仍是正常任务对象，可以准确说明、引用和给出代码；不要把这种业务讨论替换成运行标记，也不从作品中的配置推断当前运行参数。标记只代表当前回答者自己的本轮思考配置引用，不用于改写用户原话、证据、其他角色发言或任务作品。

继续输出完整原生推理及同次答案。应用保留实际响应，标记匹配仅生成有记录的读取视图；本格式约定不删除历史、不改变事实判断、不授予质量通过，也不要求你宣告检查完成。""".replace("__CONTROL_REFERENCE__", REASONING_EFFORT_REFERENCE)
