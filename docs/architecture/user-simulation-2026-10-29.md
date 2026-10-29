# 用户表达模仿与共建 Agent 底座

## 当前采用范围

目标是让同一用户在连续对话中自然接话，保留语言、语气、省略、信息披露节奏和纠正方式。推理时利用可见原话示范即可尝试，不需要为每位用户训练模型，也不能把人设属性当成其说话方式的证据。

阅读会话已有独立用户模拟和回答者。`simulator_messages` 将真实 user 轮转为模拟器的 assistant 历史，将真实助手轮转为模拟器的 user 历史，正文和顺序原样保留。只传公开正文，不传助手推理。actor 协议 2 不传整篇资料，私有故事只供用户模拟器使用；回答者继续使用自己的公开资料上下文。

`workflow.reading_user@1.2.0` 明确从自己的历史延续表达，从对方回答理解进展，避免被助手长篇讲解的语气带走。允许确认、补充、直接要求和有用的清单编号，不强迫问句、短句、错字、固定口头禅或第一人称前缀。人工在该节点模板中补入原话示范时，只借鉴语体，不把示例经历移作当前事实。

新模板沿用多轮节点的编辑、个人保存、恢复默认和冻结机制。原 1.0.0 / 1.1.0 正文逐字保留；既有任务、自定义覆盖、请求检查点和训练导出不迁移。不新增模型请求或独立评审。

首轮只有合成故事时，没有足够证据模仿特定真人。后续历史也可能来自合成模拟器，不能将“输入含历史”写成“已经证明像某个真实用户”。导入的真实 DSH 对话仍按原记录处理，本轮没有增加自动抽取个人语料或接续改写导入记录的功能。

三类阅读请求当前仍使用 `generation` 模型绑定。本轮没有暗中改成 question 槽或另一种轻量模型。独立用户模型属于后续显式配置与验证范围。

## NVIDIA 开源方案的取舍

核对日期：2026-10-29。未安装框架依赖、未下载大型数据集、未训练模型。

| 方案 | 实际用途 | 本项目的采用边界 |
| --- | --- | --- |
| [NeMo User Sim](https://github.com/NVIDIA-NeMo/UserSim) | 人设与行为驱动的多轮模拟、渐进披露和用户响应 | 借鉴用户/助手分工和行为诊断；不整体搬入其会话循环、用户门控和逐轮评审 |
| [Nemotron-Personas-USA](https://huggingface.co/datasets/nvidia/Nemotron-Personas-USA) | 英文合成人群与人物描述，HF 数据卡为 CC BY 4.0 | 作为未来人群覆盖候选；不能由职业或学历推断一个人的具体口语，更不能当作中文原话数据 |
| [Nemotron-SFT-Instruction-Following-Chat-v3](https://huggingface.co/datasets/nvidia/Nemotron-SFT-Instruction-Following-Chat-v3/blob/main/README.md) | 真人提示种子与合成多轮，用于助手指令遵循和聊天能力 | 未来底座训练候选；区分用户模拟目标与助手目标，保留来源和训练轮次掩码 |

User Sim 核对提交为 `ef1b2c083fd54f3b62e4515dfb4fba4e3bb74324`，代码为 Apache-2.0。路径为 `ConversationSimulatorGenerator → BaseProbe.run_dispatch → ConversationLoop`。其 [ProbeEpisodeRuntime](https://github.com/NVIDIA-NeMo/UserSim/blob/ef1b2c083fd54f3b62e4515dfb4fba4e3bb74324/docs/engine/EXTERNAL_PROBE_RUNTIME.md) 允许外部宿主承接 user、assistant、judge 请求，未来可独立验证与现有模型端口的适配。目前未接通。整体接管会重复本项目已有调度与检查点，并增加门控、摘要、评判调用。

官方 [methodology](https://github.com/NVIDIA-NeMo/UserSim/blob/ef1b2c083fd54f3b62e4515dfb4fba4e3bb74324/docs/methodology.md) 指出模拟模型仍可能抹平人物差异，不能替代真实用户研究。[locale 列表](https://github.com/NVIDIA-NeMo/UserSim/blob/ef1b2c083fd54f3b62e4515dfb4fba4e3bb74324/docs/locales.md) 当前没有中文。中文表现必须另行验证。

HF Personas 与 NGC 扩展人设不是同一许可；后者使用 NVIDIA Dataset License Agreement。OCEAN 等行为字段的实际来源和许可应按 [官方人设说明](https://github.com/NVIDIA-NeMo/UserSim/blob/ef1b2c083fd54f3b62e4515dfb4fba4e3bb74324/docs/personas.md) 分别保留，不能把 HF 许可套到所有变体。

Chat-v3 的 chat 子集使用真人提示种子和模型扩展。官方要求每条记录只训练最后一个 assistant 轮，之前的助手回答可能是随机抽取的，不能把整段全部当优选答案。部分首轮 system/user 为 null，须按官方 [prepare_chat_prompts.py](https://huggingface.co/datasets/nvidia/Nemotron-SFT-Instruction-Following-Chat-v3/blob/main/prepare_chat_prompts.py) 和来源摘要恢复，不猜测填充。数据卡正文列 CC BY 4.0 / ODC-By，仍需保留来源级许可及归属。

## 验证口径

针对现有阅读会话、提示词注册和节点快照运行离线回归。检查真实正文的角色投影、换行空白、编号代码保留、隐藏推理与资料隔离、旧模板摘要、自定义正文、恢复和原有调用数。

本次执行 `python -m pytest tests/test_reading_session.py tests/test_workflow_node_prompts.py tests/test_prompts.py -q --tb=short`，67 项通过，耗时 15.99 秒。未调整依赖或领域接口，未扩大为全仓回归。

离线测试只证明协议、版本和接线；本轮没有付费模型对比，不能声称模仿自然度或样本可用率已经提升。后续应固定同一组原话和公开历史，在节点中切换新旧模板，比较意图延续、个人语气、合理信息披露与角色边界；不能只按句长、错字或关键词打分。未抽检不写成质量通过。
