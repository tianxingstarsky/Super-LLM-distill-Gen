# Agent 功能与维护入口

这里按实际调用路径定位功能、提示词和状态。修改一个功能时，从它的用例和提示词所有者进入；模型传输、持久化和生产调度继续共用现有设施。并非每个模型节点都是工具 Agent，也不为拆文件新增一套执行框架。

## 提示词按功能管理

[`lib/prompts/workflow.py`](../../lib/prompts/workflow.py) 只显式转发既有名称，注册表仍由 `lib.prompts.get(id, version=...)` 访问。各功能模块拥有自己的 `PromptSpec` 构造、历史别名和版本推进。下面的提示词 ID 均省略 `workflow.` 前缀。

| 功能 | 唯一版本定义入口 | 提示词范围 |
| --- | --- | --- |
| 工作流公共请求协议 | [workflow_protocol.py](../../lib/prompts/workflow_protocol.py) | `system` |
| 文档读取与 CPT | [workflow_corpus.py](../../lib/prompts/workflow_corpus.py) | `document_parse`、`document_vision`、`corpus`、`cpt_clean`、`cpt_review` |
| 开放任务与对话指导 | [workflow_planning.py](../../lib/prompts/workflow_planning.py) | `plan`、`qa_director`、`question_brief_check`、`question_user`、`question_fit` |
| SFT 回答与契约 | [workflow_sft.py](../../lib/prompts/workflow_sft.py) | `sft`、`sft_styled`、`sft_directed`、`sft_answer`、`sft_directed_check`、`style_check` |
| 普通多轮对话 | [workflow_dialogue.py](../../lib/prompts/workflow_dialogue.py) | `multiturn_user`、`multiturn_assistant`、`multiturn_consistency`、`multiturn_directed_check` |
| 资料阅读会话 | [workflow_reading_session.py](../../lib/prompts/workflow_reading_session.py) | `reading_scene`、`reading_user`、`reading_answer`；场景、用户与回答者分别定义 |
| 导入对话正文 CoT 补写 | [workflow_conversation_reasoning.py](../../lib/prompts/workflow_conversation_reasoning.py) | `conversation_reasoning_hints`、`conversation_reasoning`；v1/v2/v3 沿用各自冻结选择 |
| CoT 作者与前缀续写 | [workflow_cot.py](../../lib/prompts/workflow_cot.py) | `cot_generate`、`cot_rationale`、`cot_native_answer`、`cot_prefix_check`、`rationale_check`、`cot_directed_check` |
| 同次原生推理与答案 | [workflow_native_cot.py](../../lib/prompts/workflow_native_cot.py) | `cot_native_joint` |
| 清单、主张与工具核查 | [workflow_reasoning_checks.py](../../lib/prompts/workflow_reasoning_checks.py) | `cot_self_check`、`cot_self_check_review`、`cot_claim_check`、`cot_claim_review`、`cot_check_agent` |
| 偏好候选与设计核验 | [workflow_preference.py](../../lib/prompts/workflow_preference.py) | `alternative`、`preference_directed_check`、`preference_design_check` |
| 独立评分与打包抽检 | [workflow_review.py](../../lib/prompts/workflow_review.py) | `jev_score`、`judge`、`review_score`、`review_reference`、`package_review`、`learner_context_check`、`human_augmentation_check` |
| 已有候选修订 | [workflow_revision.py](../../lib/prompts/workflow_revision.py) | `review_repair` |
| 推理修剪 | [workflow_trim.py](../../lib/prompts/workflow_trim.py) | `trim`、`trim_check`、`trim_rules_check`、`trim_directed_check` |
| 经验与个人风格提案 | [workflow_experience.py](../../lib/prompts/workflow_experience.py) | `evolve_experience`；原 `workflow_native_cot.EVOLVE_EXPERIENCE_BODY` 只转发 |

已有的 `workflow_multiturn.py`、`workflow_repair.py`、`workflow_agent_contracts.py`、`workflow_fact_review.py`、`workflow_prefill.py` 等文件保存历史正文或正文变换，功能模块按需引用，不复制第二份。部分历史正文名称与 `PromptSpec` 别名相同，例如 `REVIEW_REPAIR_V1_7`：前者仍是字符串，后者仍从 `workflow` 兼容导出为 `PromptSpec`，不能为了合并文件改变类型。早期 1.0 模板仍由 `workflow_dialogue_legacy.py` 原样定义。

## 用例、适配器与状态归属

下表的入口是现有业务策略入口，不是新增的公开 API。跨域调用仍只使用 `api.py` / `contracts.py`，具体实现由 `lib/bootstrap/` 装配。普通批次和生产轮次共享节点用例；尚在旧运行时的算法明确列出。

| 功能 | 业务入口与实际调用路径 | 状态归属与停止条件 |
| --- | --- | --- |
| 文档输入 | `TrainingWorkflowRuntime.prepared_source_units` → `iter_source_units` / `WorkflowSourceLibrary`；文档解析继续复用 documents 域与既有读取适配器 | 来源缓存与运行 `ingest` 检查点；来源无效则隔离，不把转录成功当事实认证 |
| CPT | `TrainingWorkflowRuntime.cpt` → `WorkflowCPTProcessing` 中的清洗与核查 | `cpt` 阶段记录、证据和重复谱系；仍是旧节点算法，本次未搬入新用例 |
| 开放任务规划 | 普通批次由 `TrainingWorkflowRuntime.plan/_plan_rows`；生产窗口由 `WorkflowProduction._production_plan` / `_production_plan_soft` | 规划身份与窗口检查点；资料耗尽及无新增情境沿用既有停止合同 |
| 指导员与问题角色 | `WorkflowQADirector.directed_generation` → 批次设计、`_closed_book_question` / `next_dialogue_step` | `qa_contract`、真实对话状态、来源行范围和任务身份；指导员简报不冒充用户发言 |
| SFT | `bootstrap/node_generation.sft_generator` / `directed_sft_generator` → `GenerateSft` / `GenerateDirectedSft` | `sft` 阶段候选及对应请求键；导入消息、来源证据、评审决定分开保存 |
| 普通多轮 | `bootstrap/dialogue_generation.dialogue_generation` → `GenerateMultiturnDialogue.execute` → `generate` | `DialogueProgress` 是本次调用的临时状态；逐轮及整段收据持久化，自然停止与轮数上限不互相替代 |
| 资料阅读会话 | 同一 `GenerateMultiturnDialogue.execute` 按冻结配置分派到 `_reading_session` | `reading_scene_v1`、`reading_user_v1`、`reading_answer_v1` 检查点；场景私有，用户模拟器与回答者历史分别投影；结束信号与缺失输出各自处理 |
| 导入正文 CoT | `bootstrap/conversation_reasoning.conversation_reasoning_preparer` → `PrepareConversationReasoning.execute_many/execute` | 输入节点独立提示提炼与正文补写检查点；逐助手消息记录原有/合成/缺失来源，补写完成仍是 `not_assessed` |
| CoT 作者与前缀 | `bootstrap/node_generation.cot_generator` → `GenerateCot` | 作者正文、原生前缀、答案续写与评审各用原有键；不能把作者审计或成功提交当独立通过 |
| 同次原生 CoT | `GenerateCot.generate_styled` 按 `answer_mode=native_joint` → `GenerateNativeCot.execute` | 同一供应商响应的 `reasoning_content` 与 `content` 分开保存；`cot_native_joint_v1` 及内容绑定收据不变 |
| 生成中查资料 | `GenerateCot` → `GenerateWithSources.execute`；`GenerationSourceAdapter` 只提供来源读取、搜索与工具回合 | `GenerationSources` 管理已认证资料，`source_lookup_v1` 管理本作者会话；有界回合与提交结束，不混入独立评审身份 |
| 推理核查 Agent | `CheckReasoning.execute` 统一分派：v1/v2 使用清单/主张请求；v3–v5 → `InvestigateReasoning`；v6 → `ReviewSemanticReasoning` | workspace 保存条款、主张、工具反馈和待处理项；作者自检、独立核验及争议调查各有历史。`run_reasoning_session` 管理工具循环，`CheckpointedReasoningAgent` 只传输并记录单次回合 |
| 偏好数据 | `bootstrap/node_generation.preference_generator` → `GeneratePreferencePair`；质量与类型核验由 review 域 `AssessPreferencePair` 负责 | 正负例、实际评分和设计收据分开；真实失败负例不可借类型要求改写，分差不由生成端补造 |
| 自动评分/修订 | `bootstrap/automatic_review` → `ReviewCandidate`、`ReviewGeneratedCollections`；显式返工走 `ExecuteRepairBranch` | review 域拥有候选哈希、审核历史、有界修订次数、人工待审和父版本认证；修正正文不覆盖原稿证据 |
| 打包评分 | `TrainingWorkflowRuntime.package` → `_review_package_candidates`，复用已核验的审核收据 | 发布选择、抽样覆盖、完整性和导出清单；抽检通过不等于未抽中记录已评审 |
| 推理修剪 | `bootstrap/node_generation.reasoning_trimmer` → `TrimReasoning` | 原答案保持不变，修剪收据独立；有显式导入收据且正文改变时，原收据保留为 `input_only` |
| 经验提炼 | `bootstrap/experience_evolution.experience_evolution_application` → `EvolveExperience.propose/save_version`；`ExperienceEvolutionModelPort` 负责一次严格提交 | 用户独立版本库、父版、episode 引文及请求收据；提案不自动成为已采用经验，不热改运行中的配方 |

## 共用设施与隔离规则

`workflow_spec.py` 只构造 `PromptSpec`，没有业务规则；`workflow_shared_rules.py` 保存确实被多个功能复用的知识边界、精确事实评审、用户不确定性和历史版本变换。`workflow_context.py`、`workflow_reasoning.py` 等既有共享正文继续保留单一实现。共享规则的修改需要关注所有显式导入者，不应把独有规则追加到共享模块。

`RequestNodeModel` / `CheckpointedModelRequest` 负责冻结节点提示词、模型绑定、角色投影、流式日志与请求检查点；models 域的 `ExecuteModelRequest` / `ExecuteToolTurn` 负责供应商协议、重试、并发和冷却。功能模块不直接建立另一套模型客户端或调度池。

`ExecuteWorkflowRun`、`GenerateDatasetBatch`、`ExecuteProductionRounds`、`GenerateProductionBatch` 继续拥有运行与批次编排。功能拆分不重命名任务 ID、阶段、模型角色、检查点键、缓存目录或导出字段。窗口收据成功保存后才推进轮次；审核和发布仍遵守既有内容绑定。

## 兼容与验证

本次按功能迁移原赋值语句与版本构造；`workflow.py` 兼容入口无模板定义、运行状态或模型调用。没有新增运行时框架，也没有改应用循环、正文、模型参数或版本选择规则。

整理前后用真实注册表在独立 Python 进程中比较全部 `PromptSpec` 字段，并检查 `workflow` 的全部既有 `PromptSpec` 别名及既有提示词模块的字符串/模板导出。基线为 **86 个 ID、279 个版本、247 个 workflow 模板别名**；比较包括 purpose、source、template、variables、constraints、notes，而非只比较 ID。v2/v3 导入补写和用户本轮已有改动均包含在整理前基线中。

业务验证复用 `tests/test_prompts.py`、`tests/test_workflow_node_prompts.py` 与相关已有功能回归；跨目录依赖仍检查 `tests/test_ddd_architecture.py`。本次注册表等值检查不调用模型，也不证明模型效果；不为文件拆分新增大量镜像测试。
