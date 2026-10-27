# Google 自进化 Agent：实际实现与采用边界

本次完成官方仓库核对和适配分析，尚未安装或移植框架，也未把其提示词加入本次两轮对照。对照使用原生草稿校准模板 1.1.0，只有两次 V4.1-Flash 回答请求，没有独立评审。

## 已确认的项目

| 项目 | 实际公开内容 | 对数简立方的用途 |
| --- | --- | --- |
| [Project Chrysalis 官方介绍，2026-10-22](https://developers.googleblog.com/en/architecting-governed-self-evolving-ai-agents-on-gemini-agent-platform/)；[代码](https://github.com/GoogleCloudPlatform/generative-ai/tree/main/agents/self-evolving-ai) | ADK / Gemini 运维参考应用，声明 Apache-2.0；工具修改、经验提炼、拓扑更新。含本地模拟、预置故障及可选云服务。 | 借鉴“修改提案→可观察反馈→版本化经验→验证后更新”的闭环，不直接引入 SRE 环境与云套件。 |
| [ReasoningBank 官方介绍](https://www.research.google/blog/reasoningbank-enabling-agents-to-learn-from-experience/)；[代码](https://github.com/google-research/reasoning-bank)；[许可证](https://github.com/google-research/reasoning-bank/blob/main/LICENSE) | Apache-2.0 研究实现。从成功与失败轨迹提炼可迁移经验，后续按任务检索。不是修改模型权重。 | 更接近生成/审核/返工经验复用；提炼、检索和去重方法可经已有端口接入。 |

## 实际调用路径限制

- Chrysalis [pyproject](https://raw.githubusercontent.com/GoogleCloudPlatform/generative-ai/main/agents/self-evolving-ai/pyproject.toml) 直接引入 ADK、Google GenAI、Vertex/BigQuery/KMS 等依赖。其 [README](https://raw.githubusercontent.com/GoogleCloudPlatform/generative-ai/main/agents/self-evolving-ai/README.md) 区分真实云调用、本地模拟和回退，不能把演示结果当成本产品质量证据。
- [credit_attribution.py](https://github.com/GoogleCloudPlatform/generative-ai/blob/main/agents/self-evolving-ai/app/evolution/credit_attribution.py) 明称 Shapley-inspired，采用前后健康指标差与成本的公式，不是完整联盟反事实 Shapley 求解，不能直接当作 CoT 因果正确性判定。
- [topology_mutator.py](https://github.com/GoogleCloudPlatform/generative-ai/blob/main/agents/self-evolving-ai/app/evolution/topology_mutator.py) 添加预定义 TrafficShapingAgent 及其工具。它证明可调整运行图，不证明能自动发明适合本产品的新工作流。
- ReasoningBank [induce_memory.py](https://github.com/google-research/reasoning-bank/blob/main/WebArena/induce_memory.py)、[memory_management.py](https://github.com/google-research/reasoning-bank/blob/main/WebArena/memory_management.py) 面向 WebArena/SWE-Bench 研究数据与 Google 客户端。适合拆出资产和方法，经本项目模型网关装配，不宜直接导入带初始化副作用的研究脚本。
- [经验提炼模板](https://github.com/google-research/reasoning-bank/blob/main/WebArena/prompts/memory_instruction.py) 关注少量、可操作、可迁移的经验，区分成功和失败，避免把具体实体或答案照抄进后续任务。

## 适配到现有工程

已有 `lib/domain/agent_experience.py` / `lib/infrastructure/workflow_agent_experience.py` 负责用户可编辑、随任务固定的工作经验。保留其任务与缓存合同。后续新增记忆提炼/检索用例应进入 workflows 域，由组合根接入现有模型网关和持久化，而不是另设一套执行引擎。

实际接线存在一个要先处理的缺口：`CheckpointedModelRequest.messages` 的 reading/native 分支早于通用 `experience_system` 投影返回；因此仅向旧经验库写入内容，不会自动影响这两条新原生路线。阅读会话中若采用经验，须将实际检索到的规则冻结进公开系统上下文，并保持请求与训练导出逐字一致，不能事后给记录补一段模型没看过的经验。

建议最先采用以下有限范围：

1. 从已定位原句的审核问题及返工记录提出经验候选，附来源版本、适用条件、反例和证据；模型自称成功不自动成为成功标签。
2. 相同问题合并，依当前任务检索少量相关经验，不把所有历史审核和旧答案塞入每次请求。
3. 用户模板仍可编辑与恢复；采纳经验保存新版本，已提交任务不变。
4. 用未参与提炼的留出任务比较有/无经验的缺陷率、返工次数、调用数、耗时和 token。未验证经验可撤销，不由 Agent 自检声明自动上线。

本次不引入代码热更新或自动重绑工作流，尚未建立它们能减少本项目已确认缺陷的证据。也不将向量检索等同于省掉提示词：实际选中的经验仍须以文本/工具内容进入上下文，节省与否以 usage 及结果衡量。

## 本次真实复跑记录

`.dataforge/software-ops-2026-10-24/reading-draft-calibration-live/` 保存固定两轮原话的请求、完整原生思考、答案及逐次消耗。草稿修正了局部计数和评测范围，最终答案仍出现覆盖保证、标签偏置必然性及缺少适用依据的比例建议。报告明确记录未独立评审，不能据此宣称已解决过强结论。
