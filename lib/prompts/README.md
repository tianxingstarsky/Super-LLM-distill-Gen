# 版本化提示词

`workflow_dialogue_contexts.py` 持有通用与五类对话风格的初始正文，只按交流方式组织。指导员 1.16 在显式上下文版本下使用短目录卡片、按轮选择；不规定长短或永久人设。正文仅在选中后进入本轮原生上下文。1.15 及缺少版本标记的任务保持原请求。

`workflow.memory_generalization@1.1.0` 用 `## Contexts` 在完整经验条目中标注上述粗类别，通用经验单列 common；不增加提炼调用，不因添加类别制造新规律。旧 1.0 正文保留，实际策略版本绑定两层缓存。

新建议评审采用 `workflow.review_score@1.28.0`：判断实际缺陷，不要求每条评语都给出可复用经验，也不把补条件作为固定修法；纠正判据、推导、删除或撤回无依据主张同样有效，真实必要条件遗漏仍须指出。旧 1.27 的正文及冻结任务不变。

通用经验指导由 `workflow_experience.py` 持有。`workflow.evolve_experience@1.1.0` 用于自定义提炼，`workflow.memory_generalization@1.0.0` 在 Google 原版提炼请求的宿主边界应用；后者保留原源码与主循环，提供生成角色、父版经验和跨任务判断目标。经验的适用范围不是要求每次答案添加限制条件，具体题面与正确答案保留为案例来源。没有可靠新规律可不新增经验；协议接线通过不证明模型已学会泛化。

`workflow.review_score@1.26.0` 对新冻结的集中协议 6 同次检查全文、原生推理与实际训练上下文，以分组共享依据减少重复声明。指定复核只处理明确争议，不能替原稿补条件或抹掉未复核的全局缺陷。非协议 6 分支逐字保留 1.25 的质量规则；历史快照和个人覆盖不改。JSON 结构与覆盖范围由请求设施提供，程序仍验证每个单元、引用和前提；不以减少调用替代质量准入。

`workflow.cot_native_joint@1.6.0` 的初始 system 采用 `workflow_runtime_control.py`：本轮思考强度如需在推理中引用，使用固定标记 `[[SJ_REASONING_EFFORT]]`，具体配置由程序保存。标记不要求固定位置或每条必有；用户正常讨论参数的任务保留。训练读取按目标配置直接替换标记，原生响应与源记录保留，不需要外部工具或额外模型调用。旧模板、冻结任务和用户自定义正文不改写。实际格式遵循率仍需模型实测。

`workflow.cot_native_joint@1.5.0` 将内部制作旁白的约束同时用于原生推理与正式答案，鼓励直接核对业务主张、依赖及证据。新增材料覆盖范围规则：节选未找到只适用于节选，未知全文、页码或跨页关系不能被写成已经核查；所写任务提示词也要保持同一范围。真实用户要求的工具/API/提示词作品不作词汇封禁，不规定句式、步骤或长度。历史 1.4.0 通过 `COT_NATIVE_JOINT_V1_4` 保持逐字段冻结。

`workflow.cot_native_joint@1.4.0` 提供完整的回答者职责说明：区分真实用户、应用操作、审查员与工具观察；原生思考核对证据，正式答案交付实际任务；返工替换受审轮而非讲述后台改稿。用户需要的业务纠错解释和提示词/API 作品保留。新版取消默认“短、简洁”的正文及约束，按任务充分展开，不设字数、段数或固定思考步骤。新默认经验同步采用 v5／“充分推敲 · 初始 v2”，防止旧默认经验重新施加缩短倾向。历史模板、个人覆盖和冻结经验不改写。验证只覆盖接线与兼容，真实效果需要另看模型输出。

`workflow.rationale_check@1.11.0` 与 `workflow.review_score@1.24.0` 明确区分审查员、受审助手、原对话用户与工具证据。审查员在原有 `reason`／协议指定的逐项理由字段说明依据、缺口与修正方向，不认领受审助手的原生推理；第一人称反思由原回答模型读取反馈后自行产生。嵌套历史属于审查材料，工具传回的评语也可质疑，不能冒充已核实事实。已确认缺陷、证据不足与误判分别处理，不强制认错、措辞、篇幅或思考步骤。新版本采用 `workflow_shared_rules.REVIEWER_ROLE_FEEDBACK`，替换旧版的第一人称追加段；判决、分数阈值、JSON 字段与证据要求不变，不增加独立模型调用。旧 `1.10.0`／`1.23.0` 及 `FIRST_PERSON_REVIEW_FEEDBACK` 原文保留，冻结任务和用户自定义正文不被覆盖。离线验证只证明版本和接线，不能声称真实语义效果已经改善。

`workflow.qa_director@1.15.0` 对应冻结的 `dialogue_constraint_provenance_version=1` 续轮合同：同次返回 `user_constraint_refs` 整数索引，不再让模型撰写 `active_constraints`。程序使用完整实际用户原话生成约束列表和来源收据，拒绝助手来源及未来拟发言；`context_links` 仍用于说明指代和局部接受范围。原话认证不等于认可或事实核验，也不限定表达长短和句式。旧 `1.14.0` 全字段继续注册，不迁移正在执行的配方。验证见 `tests/test_dialogue_constraint_sources.py`。

`workflow.rationale_check@1.12.0` 与 `workflow.review_score@1.25.0` 将需要修正的 `reason` 写成自包含、可执行的注意事项，由 `workflow_review_guidance.py` 统一定义角色和证据边界。注意事项可作为实际 user 后的 system 消息；推理引用确实随训练上下文提供的注意事项不等于内部披露。无依据的设置、角色混淆和不可见来源仍需检查，意见不因以 system 传入就成为事实。审查员仍保持独立身份，判决、阈值和字段不变，不额外购买改写调用；旧 1.11.0 / 1.24.0 及全部历史正文保留。rationale_check 的新建默认可取最新模板；review_score 的任务快照仍由协议版本选择，注册新模板不等于所有旧评分路线已采用它。冻结任务和个人覆盖原样保留。

## 按功能维护

`workflow.py` 现在只显式转发兼容名称；新增版本在下面的功能所有者中维护。注册入口仍是 `lib.prompts.get(id, version=...)`，全部旧 ID、版本、正文和导出别名保持原值。实际 Agent 用例、状态与共享设施见 [功能导航](../../docs/architecture/agent-features.md)。

| 要修改的功能 | 版本定义入口 |
| --- | --- |
| 公共请求协议、无业务规则的 spec 构造 | [workflow_protocol.py](workflow_protocol.py)、[workflow_spec.py](workflow_spec.py) |
| 文档读取与 CPT | [workflow_corpus.py](workflow_corpus.py) |
| 开放任务、指导员与问题角色 | [workflow_planning.py](workflow_planning.py) |
| 指导员与用户发言共用的情境表达策略 | [workflow_user_expression.py](workflow_user_expression.py)；由 planning / dialogue 的新版本显式采用 |
| SFT 回答、契约与风格核验 | [workflow_sft.py](workflow_sft.py) |
| 普通多轮 | [workflow_dialogue.py](workflow_dialogue.py) |
| 资料阅读的场景、用户模拟与回答 | [workflow_reading_session.py](workflow_reading_session.py) |
| 导入会话正文 CoT 补写 | [workflow_conversation_reasoning.py](workflow_conversation_reasoning.py) |
| CoT 作者、原生前缀续写 | [workflow_cot.py](workflow_cot.py) |
| 同次原生推理与答案 | [workflow_native_cot.py](workflow_native_cot.py) |
| 清单、主张与工具核查 Agent | [workflow_reasoning_checks.py](workflow_reasoning_checks.py) |
| 偏好候选与类型设计 | [workflow_preference.py](workflow_preference.py) |
| 独立评分、上下文核验与打包抽检 | [workflow_review.py](workflow_review.py) |
| 审查理由作为可见注意事项的共享规则 | [workflow_review_guidance.py](workflow_review_guidance.py)，由 cot / review 的新版本采用 |
| 初始系统的运行配置引用格式 | [workflow_runtime_control.py](workflow_runtime_control.py)，原生生成显式采用 |
| 已有候选修订 | [workflow_revision.py](workflow_revision.py) |
| 推理修剪 | [workflow_trim.py](workflow_trim.py) |
| 经验与个人风格提案 | [workflow_experience.py](workflow_experience.py) |
| 多功能共用的语义边界与版本变换 | [workflow_shared_rules.py](workflow_shared_rules.py)，并复用既有 `workflow_context.py` / `workflow_reasoning.py` |

功能模块只依赖 `base`、spec 构造、显式共享规则和已有正文资产，不导入 `workflow.py` 聚合入口，不创建模型客户端或保存运行状态。历史正文模块继续保留字符串合同；例如 `workflow_repair.py` 的版本正文与 `workflow_revision.py` 的版本模板各有一份职责，不复制正文。整理的注册表等值验证覆盖全部版本与旧导出，相关业务验证继续使用现有 prompt/节点/DDD 回归。

## 当前模板与兼容说明

`workflow.cot_native_joint@1.3.0` 区分实际公开规则、用户当前要求与任务中设计的提示词／API 配置，不从示例或历史助手推断用户约束，不臆造隐藏篇幅等级。它不规定固定思考顺序或输出句式，不增加独立评审；1.2.0 通过 `COT_NATIVE_JOINT_V1_2` 保留原文。实际生成效果仍由完整保存的原生通道判断。

`workflow.qa_director@1.14.0`、`workflow.question_user@1.5.0` 与 `workflow.multiturn_user@1.7.0` 按本轮交流动作更新表达，替换旧版共用表达段，不把相似措辞直接认定为永久习惯，也不强制改短、改长或轮换句式。指导员直接修正旧版状态依据与多轮任务设计：真实发言优先，状态是可错摘要，助手嵌套示例中的人物偏好不能变成当前用户要求。已答内容只对仍有实际歧义的对象或条件继续；草案交付可自然结束，但不表示已实施、已验证或用户验收。旧版字段、证据约束、用户节点覆盖及冻结任务不变；1.13.0 / 1.4.0 / 1.6.0 原样注册。离线检查只能验证接线与兼容性，表达改进须查看真实续轮结果。

`workflow.qa_director@1.13.0` 修正开放需求的任务依据说明：`evidence_quotes` 是私有的逐字任务依据，`closed_book` 仅让 `visible_context` 为空，不能清空已有来源的依据。合成 `brief` 可摘录其明确目标、约束与当前条件来证明任务来源，但不因此认证外部事实或助手答案；文档任务仍按来源与选区核验。独立发言者不接收这些引文。真实调用发现 1.12.0 将“没有外部事实来源”与“不提供任务依据”混为一谈，模型重复返回空依据；1.13.0 澄清两种证据含义，未放宽程序校验，1.12.0 的冻结正文原样保留。

`workflow.qa_director@1.12.0`、`workflow.question_user@1.4.0` 与 `workflow.multiturn_user@1.6.0` 共同采用 `workflow_user_expression.py` 的情境表达策略：简洁承接与较完整的情境叙事同等有效，同一用户可随真实交流改变表达，不设长短、固定句式或切换配额。指导员通过现有 `expression_design`、`dialogue_design` 和续轮 `dialogue_state.expression_design` 给出可编辑建议；独立发言者继续只接收合规简报与公开历史。新增偏好不等于虚构经历，助手建议也不自动成为已验证事实。功能模块拥有版本，公共正文没有模型客户端或调度状态；旧版本逐字注册，自定义提示词与冻结任务保持原样。通过既有节点编辑、个人模板与默认恢复入口修改，未另加运行时强制规则或评审调用。离线验证检查版本、字段和接线，真实语言效果仍由实际对话观察。

新建导入推理策略 v3 使用 `workflow.conversation_reasoning_hints@1.1.0` 提炼待核验提示，再由 `workflow.conversation_reasoning@2.1.0` 在独立上下文中生成正文 CoT。程序提供 `response_kind` 区分工具调用前决策与文本回答，避免把尚未发生的读取结果当成调用工具的前提。补写模型不接收当前答案、完整工具载荷、提炼模型的原生推理或未来工具定义；提示不能当作事实。正文由程序存入推理字段，来源标为合成解释，完成仍为 `not_assessed`。v1 固定直接补写 `1.1.0`；v2 固定提示 `1.0.0` 与正文 `2.0.0`，历史模板、运行和用户自定义正文保留，旧草稿不静默升级。这个用例借鉴答案条件化推导，不包含 STaR 的模型微调迭代，也不声称恢复原始内部思考。

`workflow.reading_user@1.2.0` 将实际用户历史作为推理时的语体示范：角色翻转后的 assistant 是用户自己的发言，user 是帮助者的回复；只从前者延续口吻、语言、省略和表达节奏，从后者理解进展。编号只限制制作题号，不压制真实的清单、引用或代码。没有示范时不宣称模仿了具体个人，人工原话示例不成为当前事实。模板继续在多轮节点编辑、保存和恢复；1.0.0、1.1.0、冻结任务和自定义覆盖保持原文。不增加请求、评审、模型路由或训练，离线接线不证明真实模仿能力。取舍与后续 NVIDIA 参考见 [用户模拟](../../docs/architecture/user-simulation-2026-10-29.md)。

`workflow.reading_answer@1.2.0` 与 `workflow.cot_native_joint@1.2.0` 默认引导短、简洁、全面的原生思考：保留必要支持、影响判断的反证、条件与实际修正，压缩重复探索和多份完整草稿。按任务需要保留这些内容，不要求每题凑齐正反观点、固定步骤或反思栏目；用户明确的篇幅和深度要求继续有效。仍由同一次原生调用产生思考与答案，提示词不能保证质量提升。1.0.0、1.1.0 及历史快照逐字保留。

`workflow.evolve_experience@1.0.0` 是独立的 system 提示词，读取当前风格、当前规则、用户偏好和导入的真实试错 episodes，只通过一次 `submit_experience_rules` 提议精炼风格及条件规则。单轨迹至多三条、多轨迹至多五条；规则保留适用条件、例外与逐字证据，不把未知结果、旧错误答案或个人审美包装成事实。证据核验、风格版本和原始轨迹谱系由程序处理，生成时无需重复注入整段失败轨迹。该提示词窄范围改编 ReasoningBank 的成功／失败／对照提炼模板，固定上游提交与完整 Apache-2.0 许可证见 [第三方资产声明](../../docs/licenses/README.md)；没有引入其运行框架。

`workflow.reading_answer@1.1.0` 与 `workflow.cot_native_joint@1.1.0` 在同一次原生思考中校准草稿和最终断言：区分事实、条件与建议，修正过强的范围、因果、必要性和数值阈值；依据充分的结论保持明确。规则不规定思考步骤，不触发额外评审或重试。旧 1.0.0、已提交快照与用户覆盖保留；离线接线验证不能证明语义问题全部解决。

`workflow.reading_scene` / `workflow.reading_user` / `workflow.reading_answer` 是多轮节点的可编辑资料阅读会话模板。场景规划读取资料，用户模拟器依据私有故事与真实历史开口，回答者的规则和资料会逐字随对话导出。场景和用户模板 1.1.0 修复真实试跑中的角色倒置：明确求助者身份、去掉用户模拟器的整篇论文注入，不预写台词或虚构实验结果。旧 1.0.0 和任务快照保留；模板约束本身不证明语义质量。

`workflow.cot_native_joint@1.0.0` 是原生思考及同次答案的可编辑系统要求，沿用节点模板保存／恢复／冻结。它引导角色、证据和自然自查，不要求固定推理步骤或模型输出结构化审计。OpenThoughts 的教师生成及 NeMo Skills 的生成／评估分工是参考；这里复用现有 SDK 运行时，没有声称导入它们的整个框架。原生通道是否遵循要求须检查真实输出，系统提示不能保证其无泄露或自动正确。

`workflow.cot_generate@1.12.0` 为可选的 `feedback.draft_to_finalize` 定义联合定稿职责：以真实任务与证据核对草稿，同步修订完整推理、答案与审计，不为旧结论补条件，不强迫正确稿改写。旧 1.11.0 保留；两个作者模板仍在 CoT 节点编辑、冻结和恢复。此提示借鉴证据支持的修订原则，不宣称仅靠自检实现 SCoRe 的训练结果（https://arxiv.org/abs/2409.12917）。无外部反馈的自我纠正存在失败风险（https://arxiv.org/abs/2310.01798），定稿收据因此不能作为独立质量通过。

`workflow_semantic_review.py` 为版本 6 提供 `workflow.cot_check_agent@1.4.0`。允许无实质影响的措辞、正常推导省略与灵活论证形式，不强制每个数字另填表；完整正文、用户规则、数值和事实边界仍须核查。专门裁决原稿缺陷、证据不足和评审误判，不能补造条件。1.3.0 与更早模板继续冻结注册。

`workflow_reasoning_environment.py` 是清单版本 5 / 配方 27 使用的 `workflow.cot_check_agent@1.3.0`。环境记录定义阶段、权限、来源范围与待核查条款；模板说明证据及反馈含义，不规定思考或工具调用顺序。模板可通过原节点提示词库修改；规则的适用条件和证据真实性仍由领域层核验。旧 1.2.0 逐字注册，不改变既有任务。

`workflow_reasoning_agent_recovery.py` 是 `workflow.cot_check_agent@1.2.0`，由清单版本 4 / 配方 26 选择。它说明批量提交、局部纠错、否定与引用绑定、逐项观察以及不可豁免的缺口。原 1.0.0 仍冻结给旧任务，生成模板不受此核查模板升级影响。

`workflow_reasoning_agent.py` / `workflow.cot_check_agent@1.0.0` 随配方 25、清单版本 3 固定。该模板定义目标、证据意义和工具提交合同，不规定检查顺序；来源按需读取，核查项逐项保存，工具给出局部反馈。原有规则和所有旧提示词继续注册；复制历史任务保留其核查版本。新建页面启用自检时使用 Agent 会话，模板仍在 CoT 节点编辑、恢复或保存到个人库。

`workflow_reasoning_checklist.py` 提供独立的作者自检和证据核查模板，随配方 23 冻结，均能在 CoT 节点编辑或恢复默认。规则清单随节点配置保存，个人清单通过既有提示词库持久化。旧配方 22 及以前的模板集合和恢复行为不变；早期新旧模板对照未开启清单，不能据该实验声称清单已改善模型质量。

`workflow_reasoning_claims.py` 为配方 24 / 清单版本 2 提供主张级自检和独立核验。要求逐句覆盖、明确单位与操作数、反例和前提范围；版本 23 的自检正文不变。新模板仍在 CoT 节点编辑，不把旁路审计作为训练推理。2026-10-25 的四个最小对照与十题真实运行单独保存在本地验证目录，效果以真实回执为准。

主张模板 `1.2.0` 明确 `percent_of` 与增幅的区别、十进制字符串字段、分批覆盖及一次评审格式修复；移除与验证论文相同的教学数字，避免把正确数值提前写进评审指令。`1.0.0` / `1.1.0` 仍逐字注册，已冻结的任务继续使用原版本。

`workflow_reasoning_workshop.py` 是新撰写解释的正向示范：任务相关草稿、支持桥梁、具体核查及条件收敛。SFT、分角色回答、多轮、CoT 合并生成和推理前缀均注册新版本；原版本逐字保留，用户覆盖仍完全有效。示例不充当业务事实，不规定固定步骤或字数，普通原生蒸馏不额外编写解释。前缀核验与正确性核验使用对应缺口标准，不能由评审补条件。离线测试证明版本和接线，不能证明实际生成质量改善。

`workflow.review_reference@2.2.0` 与 `workflow.review_score@1.22.0` 追加 `workflow_adversarial_review.py` 中的算例作用域说明。原文明示的假设前件必须绑定候选原句，再独立核对后件的真实推导；评审自行补出的前提仍按 assumption 隔离。不得把假设算例伪称已执行结果，也不得为通过而改标来源。既有版本、字段合同和条件校验保持原样，历史任务继续使用冻结模板。

评审模板 `workflow.review_score@1.21.0` 由新传输版本选择，明确区分逐句条件裁决与普通/偏好评分；既有 `1.19.0`、`1.20.0` 文本不变。普通分数不能被误写成逐句 verdict 字段，用户覆盖仍原样保存。输出合同由程序校验，不能靠模板替代引用与覆盖检查。

`base.py` 与 `__init__.py` 管理模板注册、版本和渲染。`workflow.py` 是工作流模板的兼容导出入口，定义与版本推进由上述功能模块负责。

已提交任务固定提示词版本和哈希；更新默认模板不能改变历史任务的恢复行为。旧版本仍被引用时保留。用户自定义模板通过提示词库保存，不能混入代码默认资产。

版本 3 固定 `alternative@1.5.0`、`preference_design_check@1.1.0` 和 `review_repair@1.16.0`，文本集中在 `workflow_preference.py`。新负例从真实任务概念出发渐进跑题，类型核验必须引用用户原句和转折原句。修正接收正例的冻结互译/深度要求及窗口质量意见；真实失败负例必须原样保留。全部模板仍可在节点上覆盖和还原；历史版本的文本与字段合同不变。

新 `preference_design_version=1` 固定 `alternative@1.3.0`、`preference_directed_check@2.1.0` 与 `review_repair@1.14.0`。生成、偏差设计核验与匿名质量评分职责分开；答非所问与相反结论不是同一种偏差。模板均在原节点编辑，正例补齐任务所需推导，不靠固定步骤或机械扩写。没有该版本标记时保留旧模板选择。

版本 2 固定 `alternative@1.4.0`、`preference_directed_check@2.2.0` 和 `review_repair@1.15.0`，保留版本 1 的六字段合同，并为新任务添加负例推理是否解释自身回答及其逐字引文。模型不能用“我未回答原问题”这样的制作旁白代替训练推理。类型裁决的原句绑定不等于事实真值保证。

新建显式偏好配置同时固定 `preference_design_prompt_version=1`：类型核验改用独立的 `workflow.preference_design_check@1.0.0`，`workflow.preference_directed_check` 恢复优选回答的问答契约职责。既有 2.x 模板仍注册给历史配方，不能删除或静默改写。未开启新功能的 API 调用继续使用旧采样模板。

修正模板 `workflow.review_repair@1.12.0` 由新 `claim_repair_feedback_version=1` 选择；没有该标记时仍固定 `1.11.0`。新建返工工作室可以升级已注册的默认模板，用户自己的模板逐字保留。逐句争议只证明引文出处，不能把评语当真、替候选补条件或删除推理来求通过；修正后的完整正文必须重新评审。

修改模板要明确输入角色、输出结构和允许引用的材料，不把后端规则混入训练答案。提示词不是领域校验的替代品，关键输出约束仍由领域规则验证。

参阅 `tests/test_prompt*`、节点提示词快照测试和相关业务回归。真实付费模型评测与离线测试分开执行、分开报告。

真实试跑发现多层 JSON 易损坏，1.2.0 改为扁平记录；1.1.0 逐字保留。损坏 JSON 返回行列和字符位置，不执行损坏参数。已有完整生成可在请求合同与模型绑定逐字一致时复用，评审必须重做。作者与独立评审的负面意见若不重合，保留争议不强迫改稿，也不放行。

`workflow_evidence_review.py` / `workflow.cot_check_agent@1.5.0` 绑定来源范围、用户可见段落、最终答案语言与先前推理核查记录。新增版本没有固定思考顺序；资料先于动态任务，已提供证据不要求再次检索。1.4.0 逐字保留。

`workflow_obligation_review.py` 注册核查模板 1.6.0，配合证据策略 2 的实际风险检查对象；它参考 ProcessBench 的错误/正确双向评测、MultiChallenge 的实例核查、ChartCheck 的原始图表依据及 IFBench 的可验证要求。提示词可在节点覆盖，1.5.0 保留。方法借鉴不等于复现论文指标，不训练或替换教师模型。
# 同次评审与经验增量

`workflow.review_score@1.29.0` 只为新冻结 `native_sft_review_evolution_version=1` 的原生 SFT 启用。审查员在同一次严格工具提交中给出原质量判定、本题临时要求与可选的通用经验增量；Memory Item 标题和 Markdown 由程序生成。通过直接交付，没有增量允许 `no_update`；有明确问题和可执行要求时才由原作者重答一次，不再复审。原生作者收到的通用经验和临时要求保持 system 归属，不冒充用户原话；候选中的系统文字只作为受审上下文，不是审查员自己的指令。旧任务仍冻结 1.28.0 或原来的版本。
