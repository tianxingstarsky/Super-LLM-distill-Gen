# 桌面控制台展示层

这里把用户操作转换为应用用例调用，并呈现本地缓存、任务、审核与交付的真实状态。入口在 [`lib/webapp.py`](../../webapp.py)：装配页面依赖、保持历史路由、处理跨页定位，并启动后台命令。业务执行不在页面内实现。

只面向桌面端。视觉依据在仓库 `参考/`，页面修改后用真实浏览器检查；参考图的数量、状态与文件名不能成为产品数据。

## 视觉实现归属

- [`lib/console_theme.py`](../../console_theme.py) 维护共享色板、原生控件、侧栏和页首；[`shared.py`](shared.py) 维护页首与分区标题。分区标题只决定间距，不自动给父容器添加背景或阴影。
- `*_style.py` / `*_styles.py` 只维护对应页面的具名容器。首页的创建区域、任务列表和来源目录各有层级，不给每层布局再套卡片。
- [`workflow_workbench_style.py`](workflow_workbench_style.py) 只负责创建页，运行状态使用 [`workflow_run_styles.py`](workflow_run_styles.py)。制作方式旁的链接定位实际节点画布；依赖摘要与完整依赖列表随画布展示。
- 侧栏使用 Streamlit 自带 Material 图标；历史文字图标仍用于解析旧会话标签。显示图标与持久路由不得混用。

## 按用户操作找入口

| 用户要做什么 | 页面 / 组件 | 职责与下游入口 |
| --- | --- | --- |
| 从资料或数据类型开始，找回最近工作 | [`home_page.py`](home_page.py) | `render_overview` 汇总已有任务和本地来源，设置创建入口或任务定位 |
| 导入资料、选择目标、配置节点、启动生成 | [`workflow_page.py`](workflow_page.py) | `render_workbench` 组织创建草稿和画布；执行、恢复交给工作流应用 |
| 查看流程依赖、选择节点 | [`workflow_canvas.py`](workflow_canvas.py)、[`workflow_canvas_frontend/`](workflow_canvas_frontend/) | 展示实际执行图与节点选择；不建立另一套调度顺序 |
| 在节点配置模型和解析能力 | [`workflow_node_settings.py`](workflow_node_settings.py) | `render_node_models`、`render_document_parser`；调用模型配置应用，能力探测见 [`workflow_model_capabilities.py`](workflow_model_capabilities.py) |
| 人工设计问答、反复增强 | [`human_workspace_page.py`](human_workspace_page.py) | 工作会话、设计草稿、轮次与结果；退出页面不停止正在运行的任务 |
| 找历史任务、看进度和样本报告 | [`task_management_page.py`](task_management_page.py)、[`workflow_report_panel.py`](workflow_report_panel.py) | 任务筛选与选择、真实运行状态、报告阅读；沿用工作流应用与已有报告 |
| 看节点实时输出和新产物 | [`workflow_stream_view.py`](workflow_stream_view.py)、[`workflow_live_artifacts_view.py`](workflow_live_artifacts_view.py) | 展示流式输出、调用与文件更新；不触发模型重跑 |
| 复用资料、预览训练数据、看分布 | [`asset_catalog_page.py`](asset_catalog_page.py)、[`dataset_browser_page.py`](dataset_browser_page.py)、[`sample_statistics_page.py`](sample_statistics_page.py) | 数据集应用提供文件目录、预览、分布与大小；[`source_handoff.py`](source_handoff.py) 将资料送入草稿 |
| 人工制作图片和文字样本 | [`manual_dataset_page.py`](manual_dataset_page.py) | 收集图片、问题和参考答案，调用人工数据集应用保存与导出 |
| 人工复核不同训练格式 | [`sft_review_page.py`](sft_review_page.py)、[`preference_review_page.py`](preference_review_page.py)、[`corpus_review_page.py`](corpus_review_page.py)、[`agent_review_page.py`](agent_review_page.py) | 使用各格式的审核应用，不在页面重写准入规则 |
| 保存问题、进入返工、发布审核版本 | [`review_rework_controls.py`](review_rework_controls.py)、[`review_release_controls.py`](review_release_controls.py) | 保存审核记录后进入已有返工/发布用例；保存问题不等于启动模型 |
| 下载结果与历史发布 | [`package_page.py`](package_page.py) | 展示审核发布与自动候选包，交付前由数据集应用核验 |
| 调整语言、共用连接与默认偏好 | `webapp.page_system_settings`、[`backend_page.py`](backend_page.py)、[`generation_settings_page.py`](generation_settings_page.py) | 共用连接和全局默认值；具体任务的模型、提示词仍在工作流节点选择 |
| 独立运行旧 CLI 工具和查看命令日志 | `webapp.page_run`、`webapp.page_gates`、`webapp.page_prefs`、[`monitor_page.py`](monitor_page.py) | 兼容单项工具、当前命令确认和命令偏好；不替代自动工作流 |

`webapp.py` 的公开 `page_*` 名称保留兼容。不要仅凭 `page_run` 或 `page_gates` 推断新工作流执行路径；它们是旧单项命令入口。私有 `_start_command_job` 启动后台命令，`_render_command_job_status` 只渲染命令日志，`_workspace_output_dir` / `_output_file` 只定位持久目录与文件。

## 节点设置按职责组织

| 配置内容 | 维护文件 | 边界 |
| --- | --- | --- |
| 模型选择、能力和共享并发 | [`workflow_node_settings.py`](workflow_node_settings.py)、[`model_scheduling_controls.py`](model_scheduling_controls.py) | 人工声明和探测证据分开；模型运行时由 models 域负责 |
| 文档与 DSH 会话导入 | [`docling_settings.py`](docling_settings.py)、[`conversation_import_controls.py`](conversation_import_controls.py) | 展示上传、预览、问题与确认；解析数据库不在页面实现 |
| 导入会话的缺失推理 | [`conversation_reasoning_controls.py`](conversation_reasoning_controls.py) | 输入节点选择保留、仅补缺失或排除；复用节点模型和提示词，启动时冻结，合成解释不冒充原始思考 |
| CPT、指导员、偏好与生产策略 | [`workflow_cpt_settings.py`](workflow_cpt_settings.py)、[`workflow_director_settings.py`](workflow_director_settings.py)、[`workflow_preference_settings.py`](workflow_preference_settings.py)、[`workflow_production_settings.py`](workflow_production_settings.py) | 编辑当前草稿；启动时冻结，不热改已有任务 |
| 提示词模板、导入、恢复和个人保存 | [`workflow_prompt_settings.py`](workflow_prompt_settings.py)、[`prompt_library_controls.py`](prompt_library_controls.py) | `render_node_prompts` 编辑节点实际使用的模板；`node_prompt_snapshot` 创建冻结输入 |
| 推理方式、风格、语言、深度和修剪 | [`workflow_generation_settings.py`](workflow_generation_settings.py) | 显式推理深度与模型原生思考强度分开；关闭生成保留选择，但不伪称已生成新推理 |
| 自定义思考要点与旧自检协议 | [`reasoning_checklist_controls.py`](reasoning_checklist_controls.py) | 按保存的协议解释配置；不把旧清单悄悄迁移为新行为 |
| 长期经验与轨迹进化 | [`workflow_experience_controls.py`](workflow_experience_controls.py)、[`experience_evolution_controls.py`](experience_evolution_controls.py) | 显示初始版、个人版本、DSH 轨迹提案与采用；提案不是验证成功，采用/回退保留父版 |
| 评分修正与打包评审 | [`workflow_review_repair_settings.py`](workflow_review_repair_settings.py)、[`workflow_package_review_settings.py`](workflow_package_review_settings.py) | 配置既有 review 域用例；不在控件中建立另一条返工循环 |

模型和提示词配置都留在节点内。不要新增重复总配置页。节点前后导航按实际图计算，切换时保留草稿和角色绑定。

## 页面状态、路由和兼容规则

- [`i18n.py`](i18n.py) 只负责显示语言；路由采用稳定内部标识，英文标签不能成为 `PAGES` 的查找键。历史 URL、任务定位和控件键继续兼容。
- [`app_navigation.py`](app_navigation.py)、[`review_navigation.py`](review_navigation.py) 管理跨页定位。报告进入审核、返工、导出再返回时，保留任务、阅读类型和页码。
- Streamlit session state 保存界面状态；工作流应用提供持久任务状态。创建草稿通过既有草稿服务保存，冻结配方不随界面重绘而修改。无变化的重绘不重复保存，失败后仍允许重试完整草稿。
- 对话导入格式与缺失推理策略都是持久草稿字段；更改其控件时同步检查 `creation_draft.py` 的枚举和键范围。验证需注入生产页的 DSH 导入入口，不能只覆盖没有导入器的简化工作台。
- [`work_drafts.py`](work_drafts.py)、[`workflow_reuse.py`](workflow_reuse.py) 负责恢复与复用。自动生成、人工增强各自的入口模式保留；旧草稿缺少新开关时不自动开启。
- [`selection_labels.py`](selection_labels.py) 通过稳定编号区分重名工作和样本。提交仍使用原 ID；短编号碰撞时扩展，不以显示文本决定对象身份。
- 只有全部选中来源被当前 DSH 确认回执覆盖时才省去不会运行的指导员配置；混合或未知来源沿用原流程。全部输入隔离时显示原因和调整入口，不能声称已有可用素材。
- 任务列表默认：已完成且不活跃的工作打开样本报告；运行中和待处理的工作打开过程视图。用户手动选择通过非控件状态跨页面保留。
- 文件目录默认展示资料和交付文件；运行记录通过显式开关显示。文件行提供任务编号与定位，时间排序来自真实文件状态。
- 人工图文提交时读取并校验当前输入，不要求先按回车。空输入不进入存储；同名集合的选择依赖稳定 ID。

## 推理、经验和结果的准确呈现

`workflow_generation_settings.py` 区分五种 CoT 方式：原生思考同次回答、显式推理与答案、保真风格改写、推敲后定稿试验、原生前缀续写。它们的请求次数、原稿与交付内容不同，不能统一标成“原生思考”。

当前未保存自检版本的新草稿使用生成中推敲（协议 7）。关闭“自定义思考要点”仍保留内置指引，不因此购买独立评审。旧协议 1–6、证据策略和风格准入值按原记录恢复；只有用户显式切换才改变新任务。`[prefix]` / `[final]` 阶段标记与 `? ` 可不适用标记在编辑、恢复、个人模板保存时保留。证据策略 2 的指定语言与风格要求仍按原合同执行。

原生同次回答、协议 7 下的保真改写可配置内容抽检或逐条核验；新选择的抽检初值为 20%，已有任务恢复原值。未抽检必须显示未核验，生成完成、自查完成和质量通过不能混用。保真改写保留完整原稿，NeMo / OpenThoughts 导入的推理与答案需有明确边界；原稿对照进入报告，不混入训练正文。

“审慎探索”等内置风格可载入编辑框并保存个人模板；超过 4,000 字符保留原输入并提示，不截断。多轮“资料阅读与情景对话”在本节点编辑场景、用户、回答三套提示词；资料和规则会随该模式导出，故事只供模拟器，轮数是上限。

长期经验候选只代表提案。采用与回退保存新版本；错误轨迹可供提炼，但不能因此获得训练导出资格。具体收集、提炼、采用与可选返工能力由经验应用提供，页面不能把尚未接通的自动更新路径描述成已经运行。

报告、审核与输出必须区分自动候选、审核发布和返工子版本。返工按子任务实际目标展示，不继承父任务全部分类。已有人工审核发布优先展示，默认选中审核后的训练文件；原包与历史发布仍可访问。读取报告不改写产物；保存全部审核决定成功后才能进入返工。

[`workflow_recipe_controls.py`](workflow_recipe_controls.py) 按需下载完整冻结配方。界面只显示摘要，不将多兆字节原稿或核查 JSON 反复发入折叠区。现成 ZIP 优先下载，下载前重新核验，不能用页面旧状态代替交付检查。

## 依赖与样式归属

应用对象从 [`lib/bootstrap/`](../../bootstrap/README.md) 注入，跨域逻辑走各域 `api.py`。页面不定义生成策略、去重、审核准入、缓存身份或发布条件，也不创建另一份模型调度器。

[`shared.py`](shared.py) 提供页首和小节标题，`*_style.py` / `*_styles.py` 负责页面视觉，公共主题在 [`lib/console_theme.py`](../../console_theme.py)。[`page_motion.py`](page_motion.py) 只管理页面切换动效。样本/报告的大小显示复用 [`file_sizes.py`](../file_sizes.py)，原始字节值仍供排序与导出。

## 按影响路径验证

只运行本次改动涉及的路径；下表是定位索引，不是每次必须全部执行的清单。界面改动还需真实桌面截图与操作检查。

| 改动范围 | 现有验证入口 |
| --- | --- |
| 控制台入口、导航和旧链接 | `tests/test_console.py`、`ui_tests/test_console_ui.py`、`ui_tests/test_human_workspace_navigation.py` |
| 导入与节点操作 | `ui_tests/test_conversation_import_ui.py`、`ui_tests/test_workflow_operation_logic.py`、`ui_tests/test_workflow_ui.py` |
| 提示词、风格和自检恢复 | `tests/test_workflow_prompt_library.py`、`tests/test_workflow_generation_ui.py`、`ui_tests/test_reasoning_checklist_controls.py` |
| CoT 模式与核验策略 | `tests/test_cot_answer_mode_draft.py`、`tests/test_cot_review_policy.py`、`tests/test_cot_style_transfer.py` |
| 报告、返工、配方和输出定位 | `ui_tests/test_result_actions_ui.py`、`ui_tests/test_recipe_download_ui.py`、`ui_tests/test_package_page.py`、`tests/test_review_navigation.py` |
| 人工图文与同名对象 | `tests/test_manual_dataset_page.py`、`tests/test_selection_labels.py` |

修改依赖时补跑 `tests/test_ddd_architecture.py` 与相应业务回归。纯说明文字调整不跑业务测试；默认不启动全仓回归。
