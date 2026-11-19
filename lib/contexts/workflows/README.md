# 工作流

管理一次生成任务的创建、执行、恢复、停止和过程查询。数据集交付与人工审核分别属于 datasets 和 review。

独立的 `GenerateGuidedAnswer` 以当前保留稿、学生已确认的发现和已经披露的线索为快照生成续写。使用 `python -m lib.guided_answers --help` 查看导入、运行和离线导出入口。原生日志和撤回稿保存在各题档案中。需要解题说明的题在答案键匹配且说明通过审核时进入 `accepted.jsonl`。只有原推理的来源可补写正式答复，保存在 `unverified.jsonl`。回归见 `tests/test_guided_answers.py`。

从 3.4 起，程序可从单行加粗的结论标题提取数值，也能核对英文计数题结尾重述的数量和唯一 `\boxed{…}` 值。重述的数量须与原题一致。题目范围、条件或备选值不明确时保留未判，解题说明继续交由指导员核查。已有运行按清单中的协议版本恢复。

从 3.5 起，学生可见正文因输出上限截断时，整题仍进入隔离。题目预先登记的 `finite_set` 规则可以核对正文中完整、肯定的变量取值集合，原文跨度、规则来源和轮次保存在该次尝试的 `program_confirmed` 中。再次写出同一结论时会关联最早确认的段落。其他小计和整段推导保存在原始回执中，截断不会触发新调用。

从 3.6 起，可仅凭终答交付的有键题可在题源标记 `answer_requirement: "final_only"`。学生在独立首轮直接作答。程序核对完整回复，匹配后按 `training_route: "nonthinking_direct"` 保存原文，调用在此结束。有解答过程的正文或未命中的答案进入指导流程。经指导员核实的完整解答标为 `reviewed_solution`。`summary.json` 的 `accepted_by_route` 统计通过案例。4.9.1 将独立解题、修正和教师示范的监督目标另存为 `training-targets.jsonl`；旧协议的 `accepted.jsonl` 格式保留。

3.7 中，有键首稿带实际解题过程且通过审核时，`unaided_reasoning` 路线保留终答标签前的原文推导。实际收到过提示的题走 `guided_reasoning`，程序按时间编排学生每次作答、已发布提示以及此前交付失败的答案。两条路线均由原学生模型从思考结束位置续写正式回答。训练消息的 `reasoning_content` 保存已检查的推导或完整公开轨迹，`content` 保存新答案。新答的答案键须再次匹配。新答有误且尚有轮次时，学生携带自己的旧结论、交付原文及核对提示重新求解。个别正式请求的原稿和结束状态保存在每题归档中。可选的 `task_system` 保留原任务的系统约束。

若首次正式终答错误恰好落在普通学生轮次上限，`finalization` 会记录一次专用修正轮 `granted_correction_round`，其余失败按原预算结束。中文计数题的自然终句也可核对，须明确复述原题所问的对象、给出一个数值且量词或单位相合。

3.8 在学生收到提示前单独审核这条披露。3.9 先由教师根据已发布内容续写一段承接说明和一个小提示，再交给独立的披露审查请求。审查要指出题面或已成立学生发现中的逐字依据，并在已有披露时引证上一段实际发布的文字。未通过的提示最多改写、复核一次，两稿与各自凭据保存在 `attempts[].disclosure_work`。题源提示可作为私有参考；用尽后仍可依据当前进度给出新提示。采用持续指导时，缺少新依据或持续无已核实进展的题保存在 `awaiting_guidance`，适合补充证据后继续。教师长期经验写入 `experience.disclosure_teacher`，仅由教师写稿角色使用。

- 对外使用 [api.py](api.py) 中的 `WorkflowRunApplication`。
- [application](application/README.md) 决定执行顺序；[domain](domain/README.md) 决定依赖和状态含义。
- [infrastructure](infrastructure/README.md) 承担持久化、锁和阶段操作。
- 装配入口是 `lib/bootstrap/workflows.py`、`workflow_execution.py`、`dialogue_generation.py`、`production_execution.py`、`node_generation.py` 和 `workflow_requests.py`。

维护执行顺序从 `ExecuteWorkflowRun`、`GenerateDatasetBatch`、`GenerateMultiturnDialogue`、`ExecuteProductionRounds` 和 `GenerateProductionBatch` 开始。SFT、CoT 和推理修剪分别由 `GenerateSft`、`GenerateDirectedSft`、`GenerateCot`、`TrimReasoning` 处理；`GeneratePreferencePair` 负责版本化偏好构造；`RequestNodeModel` 管理一次节点请求与检查点。评分修复用例属于 review 域；供应商请求重试属于 models 域。CPT、部分旧版偏好路径及资料缓存仍有旧实现，不能把已有拆分理解为整个引擎已经迁移。

查找“要调用哪个服务”时，从 [组合根导航](../../bootstrap/README.md) 按业务操作定位。`api.py` 还公开来源辅助生成、配置的推理检查及经验版本用例；它们不是页面启动时自动执行的后台任务。

验证：`tests/test_workflow_execution_application.py`、`tests/test_workflow.py`、`tests/test_workflow_production.py`、`tests/test_workflow_reasoning_route.py`。

节点与请求验证：`tests/test_generation_node_use_cases.py`、`tests/test_node_request_application.py`，以及 SFT、CoT、提示词角色、原生推理和恢复回归。

`api.py` 同时公开 `ImportConversations`：先预览经过外部适配器还原的会话，再确认选入持久资料缓存。手动会话库目前只接 DSH；供应商格式解析属于独立插件，不能进入核心用例。验证见 `tests/test_conversation_import.py` 和 `tests/test_dsh_import_ingestion.py`。
