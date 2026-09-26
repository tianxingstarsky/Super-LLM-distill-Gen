"""Small, session-scoped translations for the Streamlit interface."""
from __future__ import annotations

from functools import wraps
import html
from html.parser import HTMLParser
import re
from typing import Any


ZH_EN: dict[str, str] = {
    "数简立方": "ShuJian Cube",
    "数据简单生成": "Simple data creation",
    "·　数据简单生成": "· Simple data creation",
    "数简立方控制台": "ShuJian Cube Console",
    "界面语言": "Language",
    "选择控制台显示语言。": "Choose the language used across the console.",
    "首页": "Home",
    "总览": "Overview",
    "数据生成": "Create Data",
    "自动工作流": "Workflows",
    "数据管理": "Data Library",
    "资产管理": "Assets",
    "数据预览": "Data Preview",
    "质量报告": "Quality Report",
    "人工审核": "Human Review",
    "任务管理": "Task Center",
    "管线运行": "Pipeline Run",
    "运行监控": "Run Monitor",
    "监控": "Monitor",
    "输出打包": "Release Packages",
    "模型与密钥": "Models & Keys",
    "系统设置": "Settings",
    "系统设置视图": "Settings view",
    "闸门": "Gates",
    "偏好设置": "Preferences",
    "当前工作区": "Current workspace",
    "历史数据（default）": "History (default)",
    "工作区路径与存储位置": "Workspace paths and storage",
    "当前来源": "Current sources",
    "工作区": "Workspace",
    "工作流": "Workflow",
    "数据管理控制台": "Data Library",
    "打开Data Library": "Open Data Library",
    "打开已有文件夹…": "Open existing folder…",
    "打开已有文件夹": "Open existing folder",
    "打开文件夹": "Open folder",
    "浏览本机文件夹…": "Browse folders…",
    "文件夹位置": "Folder location",
    "选择你已经准备好的目录。不会复制、搬走或重命名原文件。": "Choose an existing folder. Source files will not be copied, moved, or renamed.",
    "当前环境没有本机目录选择器，请粘贴目录路径。": "A folder picker is not available here. Paste a folder path instead.",
    "已有文件夹路径": "Existing folder path",
    "展开或收起菜单": "Expand or collapse the menu",
    "展开导航菜单": "Open navigation menu",
    "收起导航菜单": "Collapse navigation menu",
    "从资料到训练数据": "From source to training data",
    "自动生成 · 全程可追溯": "Automatic creation · Fully traceable",
    "人工智能数据生成与管理平台": "AI Data Creation and Management",
    "从文档、智能体上下文和开放需求，构建可追溯的高质量训练数据。": "Create traceable training data from documents, agent context, and open briefs.",
    "从文档、智能体上下文和开放需求，构建可追溯的高质量训练数据。 ": "Create traceable training data from documents, agent context, and open briefs. ",
    "生成类型": "Choose a source",
    "选择输入来源，进入自动工作流。": "Choose a source to start a workflow.",
    "文档资料": "Documents",
    "上传资料并保留原文位置。": "Upload files and keep source locations.",
    "Agent 上下文": "Agent Context",
    "导入对话、工具调用和观测。": "Import conversations, tool calls, and observations.",
    "开放需求": "Open Brief",
    "领域 · 场景 · 任务": "Fields · scenarios · tasks",
    "描述场景，生成多样化候选。": "Describe a scenario and create varied examples.",
    "开始配置 →": "Configure →",
    "数据生成工作台": "Data Creation Workspace",
    "进入数据生成工作台 →": "Open data workspace →",
    "进入数据生成工作台": "Open data workspace",
    "训练策略选择": "Choose training goals",
    "按目标预填配方，进入后仍可调整。": "Start with a suggested plan. Adjust each goal at any time.",
    "持续预训练": "Continued pretraining",
    "清洗与分块语料": "Clean and organize domain text.",
    "选用 CPT →": "Choose CPT →",
    "监督微调": "Supervised fine-tuning",
    "指令与对话": "Instructions and conversations",
    "指令对话与多轮": "Instructions and multi-turn conversations",
    "选用 SFT →": "Choose SFT →",
    "偏好优化": "Preference optimization",
    "优选与对照回答": "Preferred and comparison answers",
    "选用 DPO →": "Choose DPO →",
    "工具轨迹": "Tool trajectories",
    "使用自动推荐方案": "Use recommended plan",
    "任务统计": "Task Summary",
    "当前工作区的真实运行状态": "Live activity in this workspace",
    "工作流总数": "Total workflows",
    "已完成": "Completed",
    "处理中": "In progress",
    "需检查": "Needs review",
    "查看全部任务 →": "View all tasks →",
    "工作流总数 ": "Total workflows ",
    "已完成 ": "Completed ",
    "处理中 ": "In progress ",
    "需检查 ": "Needs review ",
    "来源文件": "Source files",
    "源文件": "Source files",
    "样本文件": "Example files",
    "本地发布版本": "Local releases",
    "工作流总数 ": "Total workflows ",
    "当前工作区中的部分输入资料": "Some source files in this workspace",
    "工作流": "Workflow",
    "从输入到训练包，每一步都可查看状态、证据与产物。": "Follow every step from source to training package, with status and results.",
    "查看可视化工作流 →": "View workflow →",
    "输入数据": "Add source",
    "文档 / 对话 / 需求": "Documents / conversations / briefs",
    "数据解析": "Parse source",
    "抽取 / 分块": "Extract / split",
    "数据处理": "Create data",
    "生成 / 清洗": "Generate / clean",
    "质量审核": "Review quality",
    "自动 / 人工": "Automatic / human",
    "输出数据": "Export data",
    "校验 / 打包": "Check / package",
    "人工审核 / 模型对齐": "Human Review / Model Alignment",
    "逐条查看来源与模型判断，确认后再发布训练版本。": "Review sources and model results before releasing a training version.",
    "指令数据调整": "Instruction Data Review",
    "检查对话上下文、修订回答并留痕。": "Check context, revise answers, and keep a record.",
    "偏好对优化": "Preference Pair Review",
    "比较候选回答，确认两类偏好方向。": "Compare answers and confirm the preferred response.",
    "语料审阅": "Corpus Review",
    "核对原文位置、质量信号与保留范围。": "Check source locations, quality signals, and retained text.",
    "进入人工审核": "Open human review",
    "输出与存储": "Exports and storage",
    "已生成版本和工作区位置": "Released versions and workspace location",
    "查看输出打包": "View release packages",
    "暂无最近任务": "No recent tasks",
    "最近任务": "Recent tasks",
    "当前工作区还没有工作流任务。选择来源或训练策略，即可开始创建。": "No workflows in this workspace yet. Choose a source or a training goal to get started.",
    "尚无来源文件": "No source files yet",
    "可以在数据生成页上传文档，也可以用开放需求直接开始。": "Upload documents or start with an open brief.",
    "暂无质量报告": "No quality report yet",
    "没有匹配的文件": "No matching files",
    "清除搜索词，或切换文件分类。": "Clear the search or choose another file group.",
    "还没有可预览的样本": "No examples to preview yet",
    "任务启动后，这里会显示阶段、耗时与执行结果。": "Stages, timing, and results will appear here after a task starts.",
    "数据分布": "Data distribution",
    "语言为字符启发式分类；长度按字符统计。": "Language groups use a character-based estimate. Length is counted by character.",
    "中文": "Chinese",
    "中英混合": "Chinese and English",
    "英文或其他": "English or other",
    "数据预览": "Data Preview",
    "统一浏览来源与产物，预览各类训练样本并查看质量检查结果。": "Browse sources and results, preview examples, and check quality reports.",
    "浏览已校验的语料、对话、偏好对与工具轨迹。": "Browse checked text, conversations, preference pairs, and tool records.",
    "按文件名或路径筛选…": "Filter by file name or path…",
    "搜索文件": "Search files",
    "文件分类": "File group",
    "文件目录": "Files and folders",
    "文件详情": "File details",
    "查看 →": "View →",
    "查看文件详情": "View file details",
    "查看路径、大小和内容摘录": "View path, size, and content excerpt",
    "选择一个文件后，可在此查看详情并下载。": "Select a file to view its details and download it.",
    "文件超过 50 MiB；请从工作区目录直接读取，或在输出打包中下载任务数据包。": "This file is over 50 MiB. Open it from the workspace or download the task package.",
    "浏览来源、对话样本、偏好数据和工作流产物。": "Browse sources, conversation examples, preference data, and workflow results.",
    "人工审核": "Human Review",
    "偏好审核与模型对齐": "Preference Review and Model Alignment",
    "候选样本": "Candidate examples",
    "候选语料": "Candidate text",
    "语料样本": "Text example",
    "偏好样本": "Preference example",
    "候选对": "Candidate pair",
    "全部": "All",
    "待处理": "To review",
    "待审核": "Pending review",
    "已通过": "Approved",
    "已退回": "Returned",
    "已跳过": "Skipped",
    "审核意见（可选）": "Review note (optional)",
    "审核记录已保存。": "Review saved.",
    "通过并保存修订": "Approve and save changes",
    "退回": "Return",
    "跳过": "Skip",
    "队列页码": "Queue page",
    "前往数据生成": "Go to data creation",
    "进入数据生成工作台": "Open data creation workspace",
    "逐条检查训练语料及其来源证据，修订通过的样本并保留完整审核记录。": "Review each example and its source. Approve or revise it with a full review record.",
    "偏好审核队列为空": "No preference pairs to review",
    "从左侧选择一对样本，或切换队列状态。": "Choose a pair from the list or select another queue status.",
    "同一提示下比较两种模型回答": "Compare two answers to the same prompt.",
    "比较模型回答、确认偏好并保留修订历史；已审核版本单独发布。": "Compare answers, confirm a preference, and keep a revision history.",
    "修订偏好回答": "Revise preference answers",
    "修订助手内容": "Revise assistant content",
    "修订语料正文": "Revise source text",
    "审核、退回或暂时跳过": "Approve, return, or skip for now",
    "任务操作": "Task actions",
    "任务详情": "Task details",
    "人工审核版本": "Reviewed version",
    "发布版本": "Release version",
    "任务进度": "Task progress",
    "名称、任务 ID 或训练目标": "Name, task ID, or training goal",
    "搜索任务": "Search tasks",
    "新建数据工作流": "New data workflow",
    "任务过程": "Task progress",
    "选择左侧任务即可查看真实运行节点、配置、日志与产物。": "Select a task to view its stages, settings, logs, and results.",
    "当前工作区暂无自动工作流": "No workflows in this workspace",
    "添加文档、Agent 上下文或开放需求后，这里会显示实际运行节点、质量结果与日志。": "Add documents, agent context, or an open brief to see workflow stages, quality results, and logs.",
    "没有符合当前筛选条件的任务。调整状态或搜索词后再查看。": "No tasks match these filters. Change the status or search terms.",
    "开始后逐步点亮": "Stages light up as work progresses",
    "接入来源": "Add a source",
    "解析清洗": "Parse and clean",
    "生成质检": "Generate and check",
    "质检与候选打包": "Check and package candidates",
    "输出打包": "Release Packages",
    "核对真实训练文件、质量证据和来源信息，导出可校验的完整数据包。": "Check training files, quality notes, and sources. Then export a complete package.",
    "确认任务完成": "Confirm the task is complete",
    "核对实际文件": "Check the files",
    "生成完整 ZIP": "Create a complete ZIP",
    "下载完整训练数据 ZIP": "Download training data ZIP",
    "生成并校验完整 ZIP": "Create and verify the full ZIP",
    "ZIP 尚未生成；下方按钮会重新校验所有文件与打包内容。": "The ZIP is not ready. The button below will check every file before packaging.",
    "核对真实训练文件、质量证据和来源信息，导出可校验的完整数据包。": "Check training files, quality notes, and sources before exporting.",
    "本地文件路径（可在文件管理器中打开）": "Local file path (open in your file manager)",
    "本地版本目录（复制后可在文件管理器中打开）": "Release folder (copy to open in your file manager)",
    "本次任务由开放需求生成，没有上传来源文件。": "This task started from an open brief. No source files were uploaded.",
    "模型与密钥": "Models & Keys",
    "端点与连接": "Endpoints and connections",
    "配置工作流调用的模型服务、角色槽位与预算；密钥只展示配置状态。": "Set model services, roles, and budgets for workflows. Secret values stay hidden.",
    "OpenAI 兼容地址": "OpenAI-compatible URL",
    "模型名（逗号分隔）": "Model names (comma-separated)",
    "模型": "Model",
    "后端": "Provider",
    "后端名": "Provider name",
    "密钥来源": "Secret source",
    "密钥（环境变量名 或 密钥值）": "Secret (environment variable name or value)",
    "保存端点": "Save endpoint",
    "保存角色": "Save role",
    "写入本地配置": "Save local settings",
    "测试连接": "Test connection",
    "选择后端": "Choose a provider",
    "选择角色": "Choose a role",
    "角色槽位": "Role slots",
    "预算与用量": "Budget and usage",
    "清零预算": "Reset budget",
    "系统设置": "Settings",
    "管理质量确认点与生成偏好，控制数据进入审核和导出之前的检查。": "Manage quality checks and generation preferences before review and release.",
    "HITL 闸门": "Human review gates",
    "生成偏好": "Generation preferences",
    "思考风格": "Reasoning style",
    "语言规则": "Language rules",
    "保存生成偏好": "Save preferences",
    "输出方式": "Output style",
    "思考内容输出": "Reasoning output",
    "高级设置：模型与评审器": "Advanced settings: models and reviewers",
    "高级编辑：YAML 原始配置": "Advanced editor: raw settings",
    "质量闸门": "Quality checks",
    "命令管线确认点": "Command workflow checks",
    "预算、数据来源与放量操作的人工确认记录": "Human approval records for budgets, data sources, and scaled releases",
    "确认进度": "Approval progress",
    "根据当前工作区状态与闸门定义实时汇总": "Live summary based on this workspace and its approval checks",
    "已确认": "Approved",
    "需处理": "Needs attention",
    "尚未触发": "Not started",
    "预算与模型闸": "Budget and model check",
    "首次生成/蒸馏前（df-run、df-distill）": "Before first generation or distillation (df-run, df-distill)",
    "数据源闸": "Data source check",
    "导入前（df-import）": "Before import (df-import)",
    "小批量放量闸": "Small batch release check",
    "批量导出前（df-export --bulk / 放量生成）": "Before bulk export (df-export --bulk / scaled generation)",
    "此确认点已通过；当时的确认参数未保存在记录中。": "This check passed. Its settings were not saved in the record.",
    "确认时间": "Approved at",
    "小批量预览报告已生成（df-preview 查看）。确认质量后放量；否则将只保留候选池。": "A small batch preview report is ready. View it with df-preview. Release after checking quality, or keep only the candidate pool.",
    "这些闸门用于命令管线；自动数据工作流还有逐阶段质检与人工审核记录。审核汇总不会自动放行 G3，批量导出仍需校验当前样本的审核覆盖。": "These checks apply to command workflows. Automated data workflows also keep stage checks and review records. Review totals do not approve G3. Bulk export still checks review coverage for the current examples.",
    "质量汇总将在生成与验证步骤结束后出现。进度会自动刷新。": "The quality summary appears after generation and checks are complete.",
    "我已核对上述条件": "I have checked these conditions",
    "确认通过 ": "Approve ",
    "待确认": "Awaiting approval",
    "未通过": "Not approved",
    "展开或收起菜单": "Expand or collapse the menu",
    "展开导航菜单": "Open navigation menu",
    "收起导航菜单": "Collapse navigation menu",
    "暂无运行日志": "No run logs yet",
    "当前工作区暂无运行日志": "No run logs in this workspace",
    "运行": "Run",
    "查看完整数据包": "View full package",
    "预览训练文件": "Preview training file",
    "选择已完成任务": "Choose a completed task",
    "选择本地发布版本": "Choose a local release",
    "选择训练目标": "Choose training goals",
    "训练目标": "Training goals",
    "运行名称": "Run name",
    "补充生成要求（可选）": "Additional instructions (optional)",
    "开始自动生成": "Start generation",
    "生成参数设置": "Generation settings",
    "添加来源": "Add a source",
    "来源与配方": "Sources and plan",
    "全部事件": "All events",
    "查看任务运行过程": "View task progress",
    "所选目标已完成，训练文件与质量报告已生成。": "The selected goals are complete. Training files and a quality report are ready.",
    "停止后续步骤": "Stop later steps",
    "继续执行 / 从断点重试": "Continue / retry from checkpoint",
    "任务": "Tasks",
    "下载所选文件": "Download selected file",
    "下载已校验文件": "Download checked file",
    "下载完整训练数据 ZIP": "Download training data ZIP",
    "选择工作流产物": "Choose workflow results",
    "选择事件": "Choose an event",
    "事件时间线": "Event timeline",
    "事件类型": "Event type",
    "事件详情": "Event details",
    "全部类型": "All types",
    "事件": "Event",
    "查看原始事件记录": "View raw event record",
    "从当前工作区已有的对话文件浏览": "Browse conversation files in this workspace",
    "自动数据生成请使用“数据生成”；这里保留原有命令工具。": "Use Create Data for automated workflows. The original command tools remain here.",
    "跟踪命令任务状态和最近输出，快速定位失败阶段。": "Track command tasks and recent output to find failed stages.",
    "查看自动工作流进度、命令运行状态与最近事件。": "View workflow progress, command status, and recent events.",
    "当前工作区已有运行任务": "Tasks running in this workspace",
    "当前工作区暂无运行日志": "No run logs in this workspace",
    "当前工作区暂无样本": "No examples in this workspace",
    "当前筛选条件下没有问题记录。": "No issues match these filters.",
    "当前身份没有此数据集的权限，请切换身份或由管理员授予权限。": "You do not have access to this dataset. Switch accounts or ask an administrator.",
    "问题类型": "Issue type",
    "问题定位": "Find an issue",
    "问题记录中的样本 ID 未在当前文件中找到，请重新选择文件后检查。": "The example ID was not found in this file. Choose another file and try again.",
    "定位问题样本": "Find examples with issues",
    "查看格式、内容与审核覆盖情况，定位需要修复或隔离的样本。": "Review format, content, and review coverage. Find examples to fix or isolate.",
    "质量报告": "Quality Report",
    "审核覆盖与放量条件": "Review coverage and release conditions",
    "审核记录仅在绑定当前样本内容时计入覆盖率。": "A review counts only when it is linked to the current example content.",
    "审核记录已保存。": "Review saved.",
    "审核身份与接入": "Reviewer access",
    "审核配置": "Review settings",
    "人工审核版本": "Reviewed release",
    "全部语料处理后开放": "Available after all text is reviewed",
    "选择候选语料查看来源": "Choose a text example to view its source",
    "正文、来源和自动质检证据": "Text, source, and automatic checks",
    "从左侧选择一条语料，或切换队列状态。": "Choose a text example or switch queue status.",
    "从左侧选择一条样本，或切换队列状态。": "Choose an example or switch queue status.",
    "从左侧选择一对样本，或切换队列状态。": "Choose a pair or switch queue status.",
    "当前没有待审 CPT 语料": "No CPT text to review",
    "当前没有待审 SFT 候选": "No SFT examples to review",
    "当前工作区没有通过产物校验的 CPT 工作流。先在“自动工作流”生成 CPT 候选，再进入语料审核。": "No verified CPT workflow is available. Create CPT examples in Workflows, then return to review.",
    "当前工作区没有通过产物校验的 SFT 工作流。先在“自动工作流”生成 SFT 候选，再进入审核。": "No verified SFT workflow is available. Create SFT examples in Workflows, then return to review.",
    "本次运行没有通过自动质量检查的语料。请查看工作流质量报告中的隔离原因。": "No text passed the automatic checks. Review the isolation reasons in the workflow report.",
    "本次运行没有通过自动质量检查的 SFT 候选。请查看工作流质量报告中的隔离原因。": "No SFT examples passed the automatic checks. Review the isolation reasons in the workflow report.",
    "本次运行没有通过自动偏好校验的候选对。查看工作流质量报告中的隔离原因。": "No preference pairs passed automatic checks. Review the isolation reasons in the workflow report.",
    "生成已审核 CPT 版本": "Create reviewed CPT release",
    "生成已审核 SFT 版本": "Create reviewed SFT release",
    "通过并保存修订": "Approve and save changes",
    "退回": "Return",
    "跳过": "Skip",
    "锁定的工具定义": "Locked tool definitions",
    "来源与自动质检证据": "Source and automatic checks",
    "以下字段来自该样本的实际生成记录": "These fields come from the generation record",
    "来源证据与原始候选保持不变": "Source evidence and the original example stay unchanged",
    "检查对话上下文、修订回答并留痕。": "Check the conversation, revise the answer, and keep a record.",
    "自动审核" : "Automatic review",
    "偏好审核队列为空": "No preference pairs to review",
    "前往数据生成": "Go to Create Data",
    "新建数据工作流": "New data workflow",
    "选择任务直接查看工作流过程": "Choose a task to follow its workflow",
    "任务过程": "Task progress",
    "任务运行中": "Task is running",
    "运行记录": "Run history",
    "运行监控": "Run Monitor",
    "本次运行没有通过自动质量检查的样本。": "No examples passed the automatic checks.",
    "所选目标已完成，训练文件与质量报告已生成。": "Selected goals are complete. Training files and a quality report are ready.",
    "数据工作流": "Data workflow",
    "预览确认": "Review plan",
    "质量审核": "Quality review",
    "产物与质量": "Results and quality",
    "来源与运行配方": "Sources and run plan",
    "来源和配方快照已固定，可用于核对与重新运行。": "The source and plan are fixed for review or another run.",
    "工作流运行图": "Workflow graph",
    "点击节点卡，检查该步骤的状态、配置与日志。": "Select a stage to review its status, settings, and logs.",
    "实时进度": "Live progress",
    "全部运行事件": "All run events",
    "按时间倒序展示最近的处理与模型调用事件。": "Recent processing and model events, newest first.",
    "节点配置": "Stage settings",
    "所选节点的运行状态与实际配方": "Status and plan for the selected stage",
    "训练产物与质量": "Training results and quality",
    "每个目标只会导出通过对应检查的样本。": "Only examples that pass the checks for each goal are exported.",
    "添加来源": "Add sources",
    "选择目标": "Choose goals",
    "自动生成": "Generate automatically",
    "审核导出": "Review and export",
    "预训练评测集去污染（可选）": "Remove evaluation overlap (optional)",
    "上传评测集参照": "Upload an evaluation reference",
    "上传自备 JSON / JSONL 评测参照，每条记录格式为 {\"text\": \"...\"}。只在本机对 CPT 候选查重，不作为训练来源，也不发送给模型；未上传时报告会标记未配置。": "Upload a JSON or JSONL evaluation reference. It stays local and is used only to check CPT examples for overlap.",
    "上传文档 / 上下文记录": "Upload documents or context records",
    "上传 PDF、DOCX、TXT 或 Markdown；解析时保留来源位置。": "Upload PDF, DOCX, TXT, or Markdown files. Source locations are kept.",
    "开放性需求": "Open brief",
    "描述任务、领域和使用场景，系统会规划并生成候选。": "Describe a task, field, or use case. The system will plan and create examples.",
    "例如：为设备维护助手生成中文训练数据，覆盖故障诊断、多轮追问与操作解释。": "Example: Create training examples for equipment repair, follow-up questions, and clear instructions.",
    "补充生成要求（可选）": "Additional instructions (optional)",
    "例如：重点覆盖故障诊断、证据引用与清晰的分步回答。": "Example: Focus on diagnosis, source citations, and clear step-by-step answers.",
    "运行名称": "Run name",
    "设置运行名称与本次处理范围": "Name this run and set its scope",
    "本次最多处理单元": "Maximum units for this run",
    "文档分块目标字符数": "Target characters per text chunk",
    "每段对话轮数": "Turns per conversation",
    "点击分类卡快速启用或清空整组，下方可逐项调整。": "Select a group to enable or clear it. Adjust individual goals below.",
    "可以同时选择多类目标；系统只会导出通过对应质量检查的样本。": "Choose several goals. Only examples that pass their checks will be exported.",
    "生成模型（留空使用模型配置）": "Generation model (blank uses saved settings)",
    "生成后端（留空使用模型配置）": "Generation provider (blank uses saved settings)",
    "生成模型与 JEV 评审器可以分开配置；留空时使用系统模型配置。": "Set the generation model and reviewer separately. Leave blank to use saved settings.",
    "高级设置：模型与评审器": "Advanced settings: models and reviewers",
    "JEV 打分后端（留空使用专用槽位）": "JEV reviewer provider (blank uses its role setting)",
    "JEV 打分模型（留空使用专用槽位）": "JEV reviewer model (blank uses its role setting)",
    "单文件最多 50 MiB，本次来源合计最多 200 MiB。": "Each file can be up to 50 MiB. Sources can total up to 200 MiB.",
    "导入完整的 JSON / JSONL 对话记录；工具轨迹需要真实观测。": "Import full JSON or JSONL conversations. Tool records need real observations.",
    "描述任务、领域和使用场景，系统会规划并生成候选。": "Describe the task and use case. The system will plan and create examples.",
    "来源与配方": "Sources and plan",
    "高级命令工具": "Advanced command tools",
    "下载本次训练数据与质量证据 ZIP": "Download training data and quality report",
    "仅用于新生成的多轮对话；导入的完整对话保持原有轮次。": "Used for new conversations only. Imported conversations keep their original turns.",
    "多轮目标逐轮及整段评审；合成内容会标记证据等级。": "Review each turn and the full conversation. Synthetic examples show evidence levels.",
    "导入完整的 JSON / JSONL 对话记录；工具轨迹需要真实观测。": "Import full JSON or JSONL conversations. Tool records need real observations.",
    "停止后续步骤": "Stop later stages",
    "已请求停止；当前模型请求返回后，在下一断点停止。": "Stop requested. The run will pause after the current model request.",
    "执行进程已中断。可从已完成的逐条断点继续。": "The process stopped. Continue from the latest completed checkpoint.",
    "运行已结束；有目标没有合格样本，或输入超过本次处理上限。查看下方质量报告。": "The run ended with a goal that has no accepted examples, or reached its input limit. See the report below.",
    "该目标目前没有通过质量检查的样本。请查看上方隔离原因。": "No examples for this goal passed the checks. Review the reasons above.",
    "任务目标": "Training goals",
    "任务类型": "Task type",
    "任务参数": "Task settings",
    "任务完成": "Task complete",
    "任务管理": "Task Center",
    "输出打包": "Release Packages",
    "质量汇总将在生成与验证步骤结束后出现。进度会自动刷新。": "The quality summary appears after generation and checks finish. Progress refreshes automatically.",
    "文档数据": "Document data",
    "结构化抽取": "Structured extraction",
    "开放任务": "Open task",
    "智能体 Agent 上下文": "Agent context",
    "开始自动生成": "Start generation",
    "或选择当前文件夹内的来源": "Or choose sources from this folder",
    "选择工作流产物": "Choose workflow results",
    "已完成任务": "Completed tasks",
    "真实样本预览": "Example preview",
    "按训练目标呈现语料、对话、偏好对或工具轨迹": "Browse text, conversations, preference pairs, or tool records by goal.",
    "这次任务没有通过质量检查的原生训练样本；请查看工作流质量报告。": "No examples passed the checks for this task. See the workflow report.",
    "清单记录了合格样本，但当前文件没有可读取的记录。": "The manifest lists accepted examples, but no readable records were found.",
    "选择任务": "Choose a task",
    "选择文件": "Choose a file",
    "生成偏好": "Generation Preferences",
    "生成参数设置": "Generation settings",
    "配置内容": "Settings content",
    "采样与质量校正": "Sampling and quality controls",
    "分布标记来源": "Distribution label source",
    "每个维度轮换模板数": "Templates per style dimension",
    "保留默认基线，其他倾向会按相对权重自动归一化。": "Keep the default baseline. Other preferences are balanced automatically.",
    "生成时根据这些描述和相对权重轮换，不把风格文本直接塞进每条样本。": "Vary these styles by their relative weight. Style notes are not copied into examples.",
    "减少模板重复，并在批次分布偏离目标时自动调整。": "Reduce repeated templates and adjust when a batch drifts from its target.",
    "输出方式": "Output style",
    "选择训练样本中推理过程的存放方式。": "Choose how reasoning is stored in training examples.",
    "语言规则": "Language rules",
    "用具体规则和修订示例约束数据的表达。": "Guide wording with clear rules and revised examples.",
    "保存原始配置": "Save raw settings",
    "保存生成偏好": "Save preferences",
    "当前尚未配置模型原生思考 token。请在下方高级编辑里填写 think_tokens 后再保存此选项。": "No model-native reasoning tokens are set. Add think_tokens in the advanced editor before saving this option.",
    "思考内容输出": "Reasoning output",
    "校正会影响后续批次的采样权重，不会硬删已生成的数据。": "Adjustments affect future batches. Existing data is not removed.",
    "训练数据配比": "Training data mix",
    "这四项表示相对倾向。保存时会在扣除默认样本占比后自动换算，不必手动凑到 100%。": "These values are relative preferences. They do not need to add up to 100%.",
    "需要修改示例、思考 token 或额外规则时，可在此编辑；保存前会检查 YAML 语法。": "Edit examples, reasoning tokens, or extra rules here. Settings are checked before saving.",
    "风格画像": "Style profile",
    "价格 JSON（可选，如 {\"input_per_1m_usd\": 0}）": "Price JSON (optional, for example {\"input_per_1m_usd\": 0})",
    "仅重置本地累计额度，不会修改外部服务账单或预算上限。": "Resets local usage totals only. It does not change provider bills or budget limits.",
    "新增或覆盖端点": "Add or replace endpoint",
    "覆盖同名后端": "Replace provider with the same name",
    "角色槽位": "Role slots",
    "选择后端": "Choose a provider",
    "选择角色": "Choose a role",
    "环境变量（推荐）": "Environment variable (recommended)",
    "密钥状态只表示本机配置是否存在。这里的主动检查才会访问对应端点。": "Secret status only reflects local settings. Connection checks contact the selected endpoint.",
    "推荐使用环境变量存放密钥。环境变量模式只保存变量名；手动密钥模式会写入本地配置。": "Use an environment variable for secrets when possible. Manual secrets are saved in local settings.",
    "添加端点后即可进行连接检查。": "Add an endpoint to test the connection.",
    "数据包尚未生成": "No package has been created yet",
    "核验文件": "Verify files",
    "格式相容不代表模型聊天模板、分词器或训练参数已经验证。": "A compatible format does not confirm model templates or training settings.",
    "自动检查产物仍需按用途进行人工复核。": "Automatically checked results still need human review.",
    "合格量来自已校验清单；原因来自本次运行记录": "Accepted counts come from the checked manifest. Reasons come from this run.",
    "完整性与来源": "Integrity and sources",
    "逐目标质量": "Quality by goal",
    "查看任务过程": "View task progress",
    "查看完整数据包": "View the full package",
    "这次任务没有记录可发布的训练目标。": "This task has no training goals ready for release.",
    "文件指纹通过，只证明产物与清单一致；内容准确性和适用性仍需按用途审核。": "A matching file fingerprint confirms the file matches its manifest. Review content for accuracy and fit.",
    "本报告只验证结构和当前内容绑定的审核记录；含图像样本还需要具备视觉能力的人工复核。": "This report checks structure and linked reviews. Image examples also need visual review.",
    "只显示前 20 条；完整记录保留在任务产物中。": "Showing the first 20 records. The full list stays with the task results.",
    "合格量来自已校验清单；原因来自本次运行记录": "Accepted counts come from the checked manifest. Reasons come from this run.",
    "以上是当前工作区可审候选量；通过、退回和待处理状态以具体队列为准。": "These are reviewable examples in this workspace. Queue counts show approved, returned, and pending items.",
    "打开审核队列 →": "Open review queue →",
    "查看生成、人工审核与发布前的确认要求。": "View generation, review, and release checks.",
    "查看任务运行过程": "View task progress",
    "内容摘录": "Content excerpt",
    "同名样本位置": "Matching example location",
    "样本内容": "Example content",
    "样本序号": "Example number",
    "样本文件": "Example file",
    "样本概览": "Example overview",
    "按对话轮次展开上下文与工具调用": "Expand conversation turns and tool calls",
    "输入样本 ID 的任意部分…": "Enter part of an example ID…",
    "按类型筛选并选择一条记录": "Filter by type and choose a record",
    "检查边界与原始问题码": "Check boundaries and issue codes",
    "高级设置": "Advanced settings",
    "展开下方配置区添加第一个模型服务。": "Open the settings below to add your first model service.",
    "尚无服务端点。展开下方配置区添加第一个模型服务。": "No model services yet. Open the settings below to add one.",
    "配置工作流调用的模型服务、角色槽位与预算；密钥只展示配置状态。": "Set model services, roles, and budgets for workflows. Secret values stay hidden.",
    "后端名": "Provider name",
    "连接状态": "Connection status",
    "密钥来源": "Secret source",
    "测试连接": "Test connection",
    "模型名（逗号分隔）": "Model names (comma-separated)",
    "OpenAI 兼容地址": "OpenAI-compatible URL",
    "写入本地配置": "Save local settings",
    "本次运行没有通过自动质量检查的语料。请查看工作流质量报告中的隔离原因。": "No text passed automatic checks. Review isolation reasons in the workflow report.",
    "导出前复核": "Review before release",
    "待处理样本仍需逐条审核。": "Pending examples still need individual review.",
    "文件扫描已达到安全上限，计数为当前已列出的数量；请缩小工作区范围查看其余文件。": "The file scan reached its limit. Narrow the workspace to find more files.",
    "当前文件没有逐条结构问题。重复统计和审核覆盖仍需单独核对。": "No structural issues were found. Check duplicates and review coverage separately.",
    "选择完整命令": "Choose a full command",
    "选择工具": "Choose a tool",
    "选择样本": "Choose an example",
    "停止后续步骤": "Stop later stages",
    "选择训练目标": "Choose training goals",
    "选择一个文件后，可在此查看详情并下载。": "Choose a file to view its details and download it.",
    "暂无运行记录。创建工作流后，这里会显示实时阶段、质量统计与产物。": "No run history yet. Stages, quality, and results will appear here after a workflow starts.",
    "尚无运行日志": "No run logs yet",
    "该事件只有类型和时间，可展开查看原始记录。": "This event has a type and time only. Expand it to view the original record.",
    # Data library metadata and categories.
    "数据视图": "Data view",
    "常用文件": "Common files",
    "全部文件": "All files",
    "工作区产物": "Workspace results",
    "训练数据文件": "Training data files",
    "偏好文件": "Preference files",
    "文件名": "File name",
    "类型": "Type",
    "来源 / 目录": "Source / folder",
    "来源": "Source",
    "产物": "Result",
    "已有资料": "Source material",
    "文件": "File",
    "位置": "Location",
    "大小": "Size",
    "修改时间": "Modified",
    "文件分类": "File group",
    "查看文件详情": "View file details",
    "查看路径、大小和内容摘录": "View path, size, and content excerpt",
    "表格": "Table",
    "图表": "Chart",
    "文件目录": "Files and folders",
    "常用": "Common",
    "语料": "Corpus",
    "样本": "Examples",
    "DPO 偏好对": "DPO preference pairs",
    "其他偏好数据": "Other preference data",
    "输入快照": "Input snapshot",
    "报告与状态": "Reports and status",
    "来源文件": "Source files",
    "搜索文件": "Search files",
    "按文件名或路径筛选…": "Filter by file name or path…",
    "没有匹配的文件": "No matching files",
    "清除搜索词，或切换文件分类。": "Clear the search or choose another file group.",
    "文件已变化或无法下载：": "The file changed or could not be downloaded: ",
    "文件超过 50 MiB；请从工作区目录直接读取，或在输出打包中下载任务数据包。": "This file exceeds 50 MiB. Read it from the workspace or download the task package.",
    "仅展示文件开头的摘录，未在这里校验全文件。": "Only the beginning of the file is shown. The full file was not checked here.",
    "为保持浏览流畅，当前显示前 ": "Showing the first ",
    " 个文件；请使用搜索缩小范围。": " files. Search to narrow the results.",
    # Generation preference controls and summaries.
    "设置训练数据的生成倾向、推理风格与语言规则。": "Set data generation preferences, reasoning style, and language rules.",
    "配置类别": "Settings category",
    "偏好配比": "Preference mix",
    "默认样本占比": "Default example share",
    "保留无风格注入的基线": "Keep a baseline without injected styles",
    "每维模板": "Templates per dimension",
    "轮换生成，减少重复": "Rotate templates to reduce repetition",
    "后验校正": "Distribution correction",
    "已开启": "On",
    "已关闭": "Off",
    "偏差超过阈值时调整下批采样": "Adjust the next batch when it exceeds the threshold",
    "推理输出": "Reasoning output",
    "分字段": "Separate fields",
    "原生 token": "Native tokens",
    "合并正文": "In the answer text",
    "仅答案": "Answer only",
    "匹配模型的思考格式": "Match the model's reasoning format",
    "默认": "Default",
    "推理与反思": "Reasoning and reflection",
    "长上下文": "Long context",
    "长上下文利用": "Long context use",
    "工具使用": "Tool use",
    "双语桥接": "Bilingual bridging",
    "双语与知识桥接": "Bilingual and knowledge bridging",
    "当前已保存的生成倾向": "Saved generation preferences",
    "批次内打乱模板顺序": "Shuffle template order within each batch",
    "开启后验分布校正": "Enable distribution correction",
    "触发校正的偏差阈值": "Correction threshold",
    "分字段保存": "Separate fields",
    "模型原生思考 token": "Model-native reasoning tokens",
    "合并到正文": "Include in answer text",
    "只保留答案": "Keep answer only",
    "分布标记来源": "Distribution label source",
    "后验分布校正": "Distribution correction",
    # Pipeline utility descriptions.
    "按任务选择工具，只填写必要参数；运行记录会保留命令与输出。": "Choose a tool and fill in the required settings. Runs keep their commands and output.",
    "将已有审核结果转换为 MiniMind 可读取的草稿文件。": "Convert reviewed results into a draft that MiniMind can read.",
    "解析文档并清洗、分块，形成可追溯语料。": "Parse, clean, and split documents into traceable corpus data.",
    "从文档生成问答候选，并保留来源片段。": "Create question and answer examples from documents with source excerpts.",
    "统计已有样本的质量问题与检查结果。": "Summarize quality issues and checks for existing examples.",
    "使用工作区审核配置处理需要专家复核的任务。": "Use workspace review settings for tasks that need expert review.",
    "拉取已配置协作服务中的待审核记录。": "Fetch pending reviews from a configured collaboration service.",
    "检查本机工作区与模型服务的运行条件。": "Check whether the workspace and model services are ready.",
    "使用完整命令目录；适合熟悉命令行参数的操作者。": "Browse all commands if you are familiar with command-line settings.",
    # Model endpoints, roles, and budgets.
    "已登记端点": "Configured endpoints",
    "可供工作流选择的模型服务": "Model services available to workflows",
    "密钥已配置": "Credentials configured",
    "仅表示凭据存在，连接需单独测试": "Credentials are present; test the connection separately",
    "已分配角色": "Assigned roles",
    "生成与评审等工作流槽位": "Workflow roles such as generation and review",
    "预算已用": "Budget used",
    "上限 ": "Limit ",
    "尚未设置预算上限": "No budget limit set",
    "当前默认：": "Current default: ",
    "服务端点提供模型": "Endpoints provide models",
    "角色槽位指定用途": "Roles define their purpose",
    "工作流按角色调用": "Workflows call models by role",
    "用量写入审计": "Usage is recorded for audits",
    "查看模型、分工与凭据状态；连接可用性以实际测试为准。": "View models, roles, and credential status. Test connections to confirm availability.",
    " 个端点": " endpoints",
    "尚无服务端点。展开下方配置区添加第一个模型服务。": "No endpoints yet. Open the settings below to add a model service.",
    "暂未设置模型": "No models set",
    "尚未分配角色": "No roles assigned",
    "默认端点": "Default endpoint",
    "密钥未配置": "Credentials not configured",
    "保存到本地覆盖文件，自动保留上一个版本。": "Save to a local override file. The previous version is kept as a backup.",
    "查看连接状态": "View connection status",
    "端点与连接": "Endpoints and connections",
    "预算与用量": "Budget and usage",
    "配置端点": "Configure endpoint",
    "保存端点": "Save endpoint",
    "端点": "Endpoint",
    "模型服务": "Model service",
    "模型列表": "Models",
    "质量评审": "Quality review",
    "视觉解析": "Vision parsing",
    "数据改写": "Data rewriting",
    "模拟环境": "Simulation",
    "JEV 独立评分": "JEV independent scoring",
    "翻译": "Translation",
    "minimind 兼容草稿导出": "MiniMind-compatible draft export",
    "文档语料整理": "Organize document corpus",
    "文档问答生成": "Generate document Q&A",
    "AI 审核小队": "AI review team",
    "协作者拉取": "Pull reviewer assignments",
    "环境自检": "System diagnostics",
    "全部命令": "All commands",
    "工作流角色": "Workflow roles",
    "角色决定数据生成、质检和辅助阶段调用哪个端点与模型。": "Roles choose which endpoint and model are used for generation, checks, and support stages.",
    "编辑角色槽位": "Edit role slots",
    "保存后，后续工作流将使用新的默认模型。": "Future workflows use the new default model after you save.",
    "已分配": "Assigned",
    "累计已用": "Total used",
    "剩余额度": "Remaining budget",
    "预算状态": "Budget status",
    "已使用": "Used",
    "预算上限": "Budget limit",
    "未设上限": "No limit",
    "预算操作": "Budget actions",
    "已用额度读取磁盘审计记录。": "Usage is read from the disk audit log.",
    "清零会留下审计记录。": "Resetting the budget adds an audit record.",
    "CPT 连续预训练语料": "CPT continued pretraining corpus",
    "仅允许修订训练正文；原始候选与来源证据不会被覆盖。": "Only the training text can be edited. The original candidate and source evidence stay unchanged.",
    "仅修订更优回答和对照回答的正文；提示与工具定义锁定。": "Only the preferred and comparison answers can be edited. Prompts and tool definitions are locked.",
    "仅允许修订助手正文与已有推理说明；其余字段锁定。": "Only the assistant response and existing reasoning notes can be edited. Other fields are locked.",
    "指令与多轮对话": "Instructions and multi-turn conversations",
    "多轮对话与上下文": "Multi-turn conversations and context",
    "Agent 失败轨迹": "Agent failure trace",
    "偏好对照": "Preference comparison",
    "AI 反馈与排序": "AI feedback and ranking",
    "算术推理题": "Arithmetic reasoning problem",
    "可见推理解释": "Visible reasoning explanation",
    "训练样本": "Training example",
    "暂无对话消息": "No conversation messages",
    "上下文指令": "Context instructions",
    "暂无轨迹消息": "No trace messages",
    "本地算术重放": "Local arithmetic replay",
    "JSON 快照重放": "JSON snapshot replay",
    "受限本地重放": "Sandboxed local replay",
    "隔离容器重放": "Isolated container replay",
    "混合工具重放": "Mixed tool replay",
    "失败截断点": "Failure cutoff",
    "本地重放已核对": "Local replay verified",
    "未记录工具返回": "Tool result not recorded",
    "重放校验": "Replay verification",
    "执行失败": "Execution failed",
    "展开工具输出": "Expand tool output",
    "空结果": "Empty result",
    "已省略中间": "Omitted middle",
    "工具返回": "Tool result",
    "工具返回 · ": "Tool result · ",
    "工具结果": "Tool result",
    "未配对的结果": "Unmatched result",
    "运行上下文": "Run context",
    "其他消息": "Other message",
    "失败轨迹": "Failure trace",
    "其他": "Other",
    "执行证据": "Execution evidence",
    "连续训练语料": "Continued pretraining text",
    "题目": "Problem",
    "计算与答案": "Work and answer",
    "结构化内容": "Structured content",
    "问题上下文": "Question context",
    "可见推理步骤": "Visible reasoning steps",
    "对话过程": "Conversation flow",
    "最终答案": "Final answer",
    "共同提示上下文": "Shared prompt context",
    "更优回答 · chosen": "Preferred answer · chosen",
    "对照回答 · rejected": "Comparison answer · rejected",
    "候选回答": "Candidate answer",
    "AI 评语": "AI feedback",
    "AI 评分": "AI score",
    "排名": "Rank",
    "思考": "Reasoning",
    "展开后续": "Show later",
    "轮对话": "conversation",
    "调用已核对": "calls checked",
    "组重复调用已剪枝": "duplicate call groups pruned",
    "轮回答已核对": "turns checked",
    "事实未独立核验": "Facts not independently verified",
    "助手回复与工具过程": "Assistant response and tool activity",
    "本轮输入": "Turn input",
    "该轮尚无回复": "No response for this turn",
    "问": "Q",
    "答": "A",
    "未知角色": "Unknown role",
    "⚠ 执行失败": "⚠ Execution failed",
    "未注明": "Not specified",
    "开发者指令": "Developer instructions",
    "系统指令": "System instructions",
    "来源：": "Source: ",
    "校验记录：": "Verification record: ",
    "运行记录会保留命令与输出。": "Runs keep their commands and output.",
    # Workflow setup, stage names, and task-state copy.
    "打开数据管理": "Open Data Library",
    "上传文档、导入 Agent 上下文，或描述开放需求；系统会自动生成、质检并进入审核。": "Upload documents, import agent context, or describe an open brief. The system creates and checks data for review.",
    "文档、对话或需求": "Documents, conversations, or briefs",
    "语料、对话、轨迹或偏好": "Text, conversations, tool records, or preferences",
    "解析、生成与质检": "Parse, create, and check",
    "人工复核后发布": "Review before release",
    "快捷方案": "Quick plan",
    "自动推荐": "Recommended",
    "预训练语料": "Pretraining text",
    "多轮对话": "Multi-turn conversation",
    "Agent 轨迹": "Agent traces",
    "偏好对齐": "Preference alignment",
    "数学推理": "Math reasoning",
    "选择SourceType": "Choose a source type",
    "快捷方案会预填下方目标；每个目标仍可单独增减。": "Quick plans preselect goals below. You can still change each goal.",
    "选择常用目标组合。下面仍可逐项增删训练目标。": "Choose a common goal set. You can still change individual goals below.",
    "按来源选择合适的输入；文档或 Agent 记录还可以附加生成要求。": "Choose a source type. Add extra instructions to documents or agent records if needed.",
    "预训练语料": "Pretraining text",
    "对话与轨迹": "Conversations and tool records",
    "推理与数学": "Reasoning and math",
    "CPT 预训练语料": "CPT pretraining text",
    "SFT 指令对话": "SFT instructions and conversations",
    "Agent 验证轨迹": "Agent verified traces",
    "DPO 偏好对": "DPO preference pairs",
    "RLAIF AI 反馈": "RLAIF feedback",
    "基础算术（GSM8K 格式）": "Basic arithmetic (GSM8K format)",
    "CoT 可见推理": "CoT reasoning text",
    "ORPO 偏好对": "ORPO preference pairs",
    "运行前流程预览": "Run plan",
    "输入解析": "Parse input",
    "CPT 语料": "CPT corpus",
    "SFT 生成": "Create SFT data",
    "偏好评审": "Review preferences",
    "质检打包": "Check and package",
    "本次包含的处理阶段；节点间的实际连接见下方依赖详情。": "Stages in this run. See dependency details below for their actual links.",
    "本次包含的阶段；节点间的实际连接见下方依赖详情。": "Stages in this run. See dependency details below for their actual links.",
    "查看完整数据依赖": "View data dependencies",
    "查看完整数据依赖 · ": "View data dependencies · ",
    "数据依赖": "Data dependencies",
    "箭头表示本次目标的真实数据依赖；各阶段仍按顺序执行。": "Arrows show data dependencies for these goals. Stages still run in order.",
    "本次来源类型：": "Source type: ",
    "运行配置摘要": "Run settings",
    "自动数据生成": "Automatic data generation",
    "先解析来源，再生成所选目标并执行质检；结束后可进入人工审核或输出打包。": "Sources are parsed before data is created and checked. Review or export the results when the run ends.",
    "尚无运行记录。创建工作流后，这里会显示实时阶段、质量统计与产物。": "No run history yet. Live stages, quality, and results will appear here after a workflow starts.",
    "查看任务运行过程": "View task progress",
    "真实样本预览": "Example preview",
    "按训练目标呈现语料、对话、偏好对或工具轨迹": "Browse text, conversations, preference pairs, or tool records by goal.",
    "这些字段来自当前选中的记录": "Fields from the selected record",
    "样本 ID": "Example ID",
    "内容类型": "Content type",
    "消息 / 用户轮次": "Messages / user turns",
    "含推理记录": "Has reasoning record",
    "工具错误": "Tool errors",
    "文件浏览仅展示原有样本内容；是否可用于训练请查看质量报告与人工审核。": "File browsing shows the original examples. Check the quality report and review queue before training.",
    "样本内容": "Example content",
    "按对话轮次展开上下文与工具调用": "Expand context and tool calls by turn",
    "任务输入": "Task input",
    "用户目标": "User goal",
    "用户": "User",
    "助手": "Assistant",
    "Agent 工具轨迹": "Agent tool trace",
    "助手输出": "Assistant response",
    "模型响应": "Model response",
    "待审核样本": "Examples awaiting review",
    "审核类型": "Review type",
    "审核队列状态": "Review queue status",
    "审核状态": "Review status",
    "任务视图": "Task view",
    "本次包含的处理阶段": "Stages in this run",
    "版本标签": "Version tag",
    "放量导出（需审核）": "Bulk export (review required)",
    "我确认清零预算（审计记录本次操作）": "I confirm the budget reset (this action will be audited)",
    "已有发布版本可在下方查看、校验和下载。上传文档、导入 Agent 上下文或描述开放需求，可生成新的训练数据包。": "View, check, and download existing releases below. Upload documents, import agent context, or describe an open brief to create a new data package.",
    "在发布前检查样本质量、修订内容并保留审核历史。": "Review example quality, revise content, and keep a full history before release.",
    "调用与返回": "Calls and results",
    "Agent 执行轨迹": "Agent execution trace",
    "仅展示文件开头的摘录，未在这里校验全文件。": "Only the beginning of the file is shown. The full file was not checked here.",
    # Task center, command runner, monitor, and workflow run labels.
    "数据工作流": "Data workflows",
    "命令管线": "Command pipeline",
    "运行日志": "Run logs",
    "文档、对话记录或开放需求": "Documents, conversations, or open briefs",
    "读取、切块与来源追踪": "Read, split, and track sources",
    "按 CPT、SFT、DPO 等目标运行": "Create data for CPT, SFT, DPO, and other goals",
    "导出可校验候选包；人工审核另行发布": "Export checked candidates. Reviewed versions are released separately.",
    "记录总数": "Total events",
    "最近记录": "Latest event",
    "显示原始记录中的实际字段": "Show fields from the original record",
    "当前工作区暂无运行日志": "No run logs in this workspace",
    "运行状态": "Run status",
    "已完成节点": "Completed stages",
    "本次目标": "Goals in this run",
    "运行尝试": "Run attempt",
    "工作流运行图": "Workflow run",
    "点击节点卡，检查该步骤的状态、配置与日志。": "Select a stage to view its status, settings, and logs.",
    "实时进度": "Live progress",
    "节点配置": "Stage settings",
    "所选节点的运行状态与实际配方": "Status and settings for the selected stage",
    "产物与质量": "Results and quality",
    "全部事件": "All events",
    "来源与配方": "Sources and plan",
    "训练产物与质量": "Training results and quality",
    "每个目标只会导出通过对应检查的样本。": "Only examples that pass the checks for each goal are exported.",
    "自动质检候选版本 · 未进行人工审核。开放需求生成的数据依赖模型评审，不能视为已核实的事实。": "Automatically checked candidates. They have not been reviewed. Data from open briefs relies on model review and is not verified fact.",
    "查看未通过原因明细": "View reasons for failed checks",
    "样本预览": "Example preview",
    "无法验证或读取该目标的训练文件，请在任务产物中检查完整性。": "The training file could not be verified or read. Check the task results.",
    "以下轨迹保留了实际执行失败证据，单独存放，不会混入通过验证的训练样本。": "These traces preserve execution failures. They are stored separately from verified examples.",
    "来源文件": "Source files",
    "开放需求": "Open brief",
    "未填写": "Not provided",
    "工作流已停止": "Workflow stopped",
    "节点开始运行": "Stage started",
    "节点处理完成": "Stage completed",
    "模型请求开始": "Model request started",
    "模型请求完成": "Model request completed",
    "工作流运行结束": "Workflow finished",
    "工作流运行失败": "Workflow failed",
    "工作流已停止": "Workflow stopped",
    "任务编号": "Run ID",
    "运行状态": "Run status",
    "可处理输入": "Ready inputs",
    "隔离输入": "Isolated inputs",
    "导出样本": "Exported examples",
    "需关注记录": "Records to review",
    "通过质检": "Passed checks",
    "隔离记录": "Isolated records",
    "当前筛选：": "Current filter: ",
    "节点错误：": "Stage error: ",
    "任务目标": "Training goals",
    # Empty review queues and review controls.
    "SFT 数据调整": "SFT Review",
    "DPO 偏好优化": "DPO Review",
    "ORPO 偏好优化": "ORPO Review",
    "CPT 语料审核": "CPT Corpus Review",
    "逐条检查样本质量、修订内容并保留审核历史。": "Review each example, revise content, and keep a full history.",
    "发布前检查样本质量、修订内容并保留审核历史。": "Review example quality, revise content, and keep a full history before release.",
    "0 个可审任务 · 候选样本": "0 reviewable tasks · candidate examples",
    "当前没有待审 SFT 候选": "No SFT examples to review",
    "当前没有待审 CPT 语料": "No CPT text to review",
    "先生成包含高质量问答的 SFT 工作流。通过自动质量检查的对话会进入这里，审核通过后可单独发布。": "Create an SFT workflow with high-quality answers first. Examples that pass automatic checks appear here and can be released after review.",
    "生成并验证文档语料后，可在这里逐条检查来源证据、修订结果并发布审核版本。": "Create and check document text first. Then review sources, revise examples, and release an approved version here.",
    "当前工作区没有通过产物校验的 SFT 工作流。先在“自动工作流”生成 SFT 候选，再进入审核。": "No checked SFT workflow is available. Create SFT candidates in Workflows, then review them here.",
    "当前工作区没有通过产物校验的 CPT 工作流。先在“自动工作流”生成 CPT 候选，再进入语料审核。": "No checked CPT workflow is available. Create CPT candidates in Workflows, then review them here.",
    "打开历史 / 导入样本审核中心": "Open legacy or imported example reviews",
    "样本进入审核的流程": "How examples reach review",
    "选择目标": "Choose goals",
    "自动生成与质检": "Create and check data",
    "仅合格候选进入队列": "Only eligible examples enter the queue",
    "人工复核": "Human review",
    "修订、通过或退回": "Revise, approve, or return",
    "SFT 指令与高质量回答": "SFT instructions and high-quality answers",
    "前往数据生成": "Go to data creation",
    "任务操作": "Review actions",
    "任务详情": "Example details",
    "任务进度": "Review progress",
    "语料审核队列": "Corpus review queue",
    "偏好对比": "Preference comparison",
    "同一提示下比较两种模型回答": "Compare two model answers to the same prompt",
    "审核、退回或暂时跳过": "Approve, return, or skip for now",
    "确认质量后提交审核结论": "Submit the review after checking the example",
    "确认偏好方向并提交结论": "Confirm the preferred answer and submit the review",
    "全部样本通过或退回后开放": "Available after all examples are approved or returned",
    "全部偏好对处理后开放": "Available after all preference pairs are reviewed",
    "跳过项仍需处理；自动候选与审核证据会保留。": "Skipped examples still need review. Candidates and review evidence are kept.",
    "跳过项仍需处理；原自动候选与评分证据会保留。": "Skipped examples still need review. Original candidates and scores are kept.",
    "跳过项仍需处理；版本只包含通过的语料，并附带审核历史与 SHA-256 清单。": "Skipped examples still need review. The release includes approved text, review history, and a SHA-256 manifest.",
    "样本": "Examples",
    "语料详情": "Corpus details",
    "选择候选语料查看来源": "Select a text example to view its source",
    "审核记录已保存。": "Review saved.",
    "尚无人工处理记录": "No review history yet",
    "当前样本等待审核": "This example is waiting for review",
    "当前语料等待审核": "This text is waiting for review",
    "审核意见（可选）": "Review note (optional)",
    "通过并保存修订": "Approve and save changes",
    "生成已审核 SFT 版本": "Create reviewed SFT release",
    "处理状态": "Review status",
    "待审核任务": "Review queue",
    "选择对话查看完整内容": "Choose an example to view the full conversation",
    "按真实轮次呈现输入、回答和工具轨迹": "View recorded turns, replies, and tool activity",
    "SFT 对话": "SFT conversation",
    "编辑助手回答": "Edit assistant response",
    "仅允许修订助手正文与已有推理说明；其余字段锁定。": "Edit assistant text and existing reasoning notes. Other fields are locked.",
    "指纹": "Fingerprint",
    "生成已审核 CPT 版本": "Create reviewed CPT release",
    "生成已审核 DPO 版本": "Create reviewed DPO release",
    "下载人工审核 SFT ZIP": "Download reviewed SFT ZIP",
    "下载人工审核 CPT ZIP": "Download reviewed CPT ZIP",
    "下载人工审核 DPO ZIP": "Download reviewed DPO ZIP",
    # Legacy quality report and output package copy.
    "样本总数": "Total examples",
    "当前选择的文件": "Selected file",
    "结构问题": "Structural issues",
    "逐条检查所记录的问题": "Recorded issues checked per example",
    "重复内容": "Duplicate content",
    "依据消息等内容指纹": "Based on message content fingerprints",
    "重复 ID": "Duplicate IDs",
    "同名样本额外出现次数": "Extra occurrences of the same example ID",
    "审核覆盖与放量条件": "Review coverage and release checks",
    "审核记录仅在绑定当前样本内容时计入覆盖率。": "A review counts only when it is linked to the current example content.",
    "正式放量仍需人工确认 G3。": "A person must still approve G3 before release.",
    "达到当前自动放量检查条件；正式放量仍需人工确认 G3。": "Automatic release checks passed. A person must still approve G3.",
    "当前未达到放量条件：": "Release checks not met: ",
    "空数据集": "Empty dataset",
    "存在结构问题": "Structural issues found",
    "存在重复样本": "Duplicate examples found",
    "有效审核覆盖不足 90%": "Review coverage below 90%",
    "审核共识未达成": "Reviewer consensus not met",
    "语言为字符启发式分类；长度按字符统计。": "Language groups use a character-based estimate. Length is counted by character.",
    "中文": "Chinese",
    "中英混合": "Chinese and English",
    "英文或其他": "English or other",
    "正文长度：最短 ": "Text length: min ",
    " 字符 · 平均 ": " characters · average ",
    " 字符 · 最长 ": " characters · max ",
    "按问题类型和样本 ID 筛选，查看原始样本及所在行。": "Filter by issue type or example ID to inspect the original example and row.",
    "问题类型": "Issue type",
    "搜索样本 ID": "Search example ID",
    "输入样本 ID 的任意部分…": "Enter part of an example ID…",
    "定位问题样本": "Find an example with this issue",
    "全部问题": "All issues",
    "图像需人工查看": "Image needs human review",
    "缺少对话消息": "Conversation messages are missing",
    "消息结构不符": "Message structure is invalid",
    "上下文起点缺失": "Context start is missing",
    "回答未完成": "Answer is incomplete",
    "未解决的工具错误": "Unresolved tool error",
    "当前文件没有逐条结构问题。重复统计和审核覆盖仍需单独核对。": "No structural issues were found. Check duplicates and review coverage separately.",
    "检查边界与原始问题码": "Check scope and raw issue codes",
    "本报告只验证结构和当前内容绑定的审核记录；含图像样本还需要具备视觉能力的人工复核。": "This report checks structure and reviews linked to current content. Image examples also need a reviewer who can inspect images.",
    "数据包尚未生成": "No package has been created yet",
    "暂无新的工作流训练包": "No new workflow packages",
    "完成的自动工作流会在这里列出可验证产物": "Checked results from completed workflows appear here",
    "当前没有可打包的完成任务": "No completed tasks to package",
    "已有发布版本可在下方查看、校验和下载。": "View, check, and download existing releases below.",
    "上传文档、导入 Agent 上下文或描述开放需求，可生成新的训练数据包。": "Upload documents, import agent context, or describe an open brief to create a new data package.",
    "前往Workflows": "Go to Workflows",
    "前往自动工作流": "Go to Workflows",
    "支持的训练目标": "Supported training goals",
    "同一批来源可产出多类数据": "One source can produce several data types",
    "文档清洗和语料整理": "Clean documents and prepare text",
    "问答和多轮上下文": "Question and answer pairs with multi-turn context",
    "重放、剪枝与失败证据": "Replay, prune, and keep failure evidence",
    "导出前检查": "Checks before export",
    "每个 ZIP 都附带校验清单": "Each ZIP includes a verification manifest",
    "仅展示完成或需检查的任务": "Completed tasks and tasks that need review",
    "逐个匹配 SHA-256 指纹": "Verify each SHA-256 fingerprint",
    "包含训练、质量与来源证据": "Includes training data, quality, and source evidence",
    "已有本地发布版本": "Local releases",
    "选择本地发布版本": "Choose a local release",
    "人工审核 · ": "Human review · ",
    "历史导出 · ": "Previous export · ",
    "人工审核发布": "Reviewed release",
    "历史导出发布": "Previous export",
    "创建于 ": "Created ",
    "清单文件": "Manifest file",
    "以下历史文件未列入原始 SHA-256 清单，因此未提供已校验下载：": "These older files are not listed in the original SHA-256 manifest, so verified downloads are unavailable:",
    "选择要打开或下载的已校验文件": "Choose a verified file to open or download",
    "下载已校验文件": "Download verified file",
    "manifest.json 记录训练文件的 SHA-256，可与下载文件独立核对。": "manifest.json records training file hashes for independent verification.",
    "质量与来源": "Quality and sources",
    "数量来自已验证清单；原因来自本次工作流质量记录": "Counts come from the verified manifest. Reasons come from this workflow's checks.",
    "合格": "Eligible",
    "候选": "Candidates",
    "无隔离原因": "No isolation reasons",
    "当前清单无训练目标。": "The current manifest has no training goals.",
    "查看来源记录": "View source records",
    "创建完整 ZIP": "Create complete ZIP",
    "输出目标": "Export goals",
    "任务状态": "Task status",
    "文件完整性": "File integrity",
    "来源任务": "Source task",
    "预览来自校验后的自动候选；正式训练前仍可进入人工审核。": "This preview uses checked automatic candidates. Review them before training.",
    "任务完成": "Task complete",
    "已完成 · 产物校验通过": "Complete · results verified",
    "已校验 · 质量需检查": "Verified · quality needs review",
    "合格样本": "Eligible examples",
    "合格产出率": "Eligible output rate",
    "清单中的目标数量": "Goal count in the manifest",
    "更新时间": "Updated",
    "当前任务": "Current task",
    "质量汇总将在生成与验证步骤结束后出现。进度会自动刷新。": "Quality details appear when generation and checks finish. Progress refreshes automatically.",
    "任务 ID：": "Task ID: ",
    "已完成 · 产物校验通过": "Complete · results verified",
    "需检查": "Needs review",
    "问答样本": "Question and answer example",
    "第": "Example ",
    "语言": "Language",
    "多轮目标逐轮及整段评审；合成内容会标记证据等级。": "Review each turn and the full conversation. Synthetic examples show their evidence level.",
    "Agent 正例需要完整的已记录工具轨迹；当前仅能重放受限整数 calculator。": "Positive agent examples need complete recorded tool traces. Only the restricted integer calculator can be replayed.",
    "描述任务、领域和使用场景，系统会规划并生成候选。": "Describe a task, field, and use case. The system will plan and create examples.",
    "上传 PDF、DOCX、TXT 或 Markdown；解析时保留来源位置。": "Upload PDF, DOCX, TXT, or Markdown files. Source locations are kept during parsing.",
    "补充生成要求（可选）": "Additional generation notes (optional)",
    "开放性需求": "Open brief",
    "上传文档 / 上下文记录": "Upload documents or context records",
    "运行名称": "Run name",
    "本次最多处理单元": "Maximum units for this run",
    "文档分块目标字符数": "Target characters per document chunk",
    "开放需求任务数": "Tasks for the open brief",
    "每段对话轮数": "Turns per conversation",
    "仅用于新生成的多轮对话；导入的完整对话保持原有轮次。": "Used for new multi-turn examples only. Imported conversations keep their existing turns.",
    "预训练评测集去污染（可选）": "Pretraining evaluation set decontamination (optional)",
    "上传自备 JSON / JSONL 评测参照，每条记录格式为 {\"text\": \"...\"}。只在本机对 CPT 候选查重，不作为训练来源，也不发送给模型；未上传时报告会标记未配置。": "Upload a JSON or JSONL evaluation set. Each record must use {\"text\": \"...\"}. It is checked locally against CPT examples, is not used for training, and is not sent to a model. Reports show when none is provided.",
    "上传评测集参照": "Upload evaluation references",
    "生成后端（留空使用模型配置）": "Generation provider (blank uses model settings)",
    "生成模型（留空使用模型配置）": "Generation model (blank uses model settings)",
    "JEV 打分后端（留空使用专用槽位）": "JEV reviewer provider (blank uses its role setting)",
    "JEV 打分模型（留空使用专用槽位）": "JEV reviewer model (blank uses its role setting)",
    "生成模型与 JEV 评审器可以分开配置；留空时使用系统模型配置。": "Set the generation model and JEV reviewer separately. Blank fields use system model settings.",
    "本次最多处理单元": "Maximum units for this run",
    "生成后端": "Generation provider",
    "选择来源类型": "Choose source type",
    "选择训练目标": "Choose training goals",
    "点击分类卡快速启用或清空整组，下方可逐项调整。": "Select a category card to enable or clear its goals. Adjust individual goals below.",
    "可以同时选择多类目标；系统只会导出通过对应质量检查的样本。": "Choose several goals. Only examples that pass the checks for each goal are exported.",
    "可选": "Optional",
    "本次包含的阶段；节点间的实际连接见下方依赖详情。": "Stages in this run. See dependency details below for their actual links.",
    "质量统计与产物": "Quality and results",
    "最近工作流": "Recent workflows",
    "尚无最近工作流": "No recent workflows",
    "预训练Corpus": "Pretraining corpus",
    "工作区产物": "Workspace results",
    "打开Data Library": "Open Data Library",
    "上限 ": "Limit ",
    "服务端点": "Model endpoints",
    "连接检查": "Connection check",
    "读取所选服务的模型列表，不发起生成请求。": "Read the selected service's model list without starting a generation request.",
    "选择服务": "Choose a service",
    "选择模型": "Choose a model",
    "服务名称": "Service name",
    "模型列表": "Model list",
    "默认模型服务": "Default model service",
    "未设置": "Not set",
    "配置模型服务后才能进行连接检查。": "Configure a model service before testing the connection.",
    "端点数量": "Endpoint count",
    "内容类型": "Content type",
    "来源位置": "Source location",
    "来源类型": "Source type",
    "来源任务": "Source task",
    "文件完整性": "File integrity",
    "SHA-256 通过": "SHA-256 verified",
    "查看任务过程": "View task progress",
    "数据导出": "Data export",
    "完整性校验": "Integrity check",
    "DATASET RELEASE　·　完整性校验": "DATASET RELEASE · INTEGRITY CHECK",
    "每个 ZIP 都附带校验清单": "Each ZIP includes a verification manifest",
    "自动检查产物仍需按用途进行人工复核。": "Automatically checked results still need human review for their intended use.",
    "训练格式": "Training format",
    "输出目录": "Output folder",
    "输入路径": "Input path",
    "更多参数（2 项）": "More settings (2)",
    "仅表示凭据存在，连接需单独测试": "Credentials are present; test the connection separately",
    "数据来源与质量检查": "Sources and quality checks",
    "状态": "Status",
    "事件时间线": "Event timeline",
    "事件详情": "Event details",
    "事件类型": "Event type",
    "选择事件": "Choose an event",
    "查看原始事件记录": "View raw event record",
    "显示最近 ": "Showing the latest ",
    "已登记端点": "Configured endpoints",
    "已分配角色": "Assigned roles",
    "预算已用": "Budget used",
    "角色分配": "Role assignments",
    "生成角色": "Generation role",
    "自动评审": "Automatic review",
    "自动质检": "Automatic checks",
    "候选总数": "Total candidates",
    "问题详情": "Issue details",
    "样本 ID：": "Example ID: ",
    "问题：": "Issue: ",
    "工具调用": "Tool calls",
    "语言规则": "Language rules",
    "默认样本占比": "Default example share",
    "输入来源": "Input sources",
    "训练目标": "Training goals",
    "任务状态": "Task status",
    "输入快照": "Input snapshot",
    "导出目录": "Export folder",
    "任务及审核产物目录": "Task and review results folder",
    "来源目录": "Source folder",
    "首选项": "Preferences",
    "未命名任务": "Untitled task",
}


def language_code(value: Any) -> str:
    """Normalize the visible choice or URL value to ``zh`` or ``en``."""
    return "en" if str(value).lower() in {"en", "english"} else "zh"


ZH_EN.update({
    "质量汇总与打包": "Quality summary and packaging", "任务数": "Task count", "处理上限": "Unit limit",
    "配方和来源快照已固定，可用于核对与重新运行。": "This run keeps fixed sources and settings. Use them to review or repeat the run.",
    "全部任务": "All tasks", "待启动 / 执行中": "Queued / running", "筛选任务": "Filter tasks",
    "未结束": "Unfinished", "按创建时间倒序": "Newest first", "运行动态": "Recent activity",
    "尚无运行事件；启动后这里会显示实际处理记录。": "No run events yet. Processing events appear after the run starts.",
    "未指定目标": "No goals selected",
    "定位运行节点": "Go to running node",
    "待启动": "Queued", "等待": "Pending", "待处理": "Pending", "执行中": "Running",
    "完成": "Completed", "已完成": "Completed", "失败": "Failed", "已停止": "Stopped",
    "已跳过": "Skipped", "未选择": "Not selected",
    "解析与来源检查": "Input and source checks", "CPT 语料整理": "CPT corpus preparation",
    "Agent 轨迹重放与剪枝": "Agent replay and pruning", "DPO / RLAIF / ORPO 偏好打分": "DPO / RLAIF / ORPO scoring",
    "GSM8K 算术核验": "GSM8K arithmetic checks",
    "生成候选": "Generate candidates", "最大处理单元": "Maximum source units", "分块目标字符数": "Target chunk characters",
    "隐私与结构规则": "Privacy and structure checks", "疑似密钥、无效结构和超长单元进入隔离": "Quarantine suspected secrets, invalid structures, and oversized units",
    "未知来源": "Unknown source", "去重": "Deduplication", "精确去重与保守近重复检查": "Exact and conservative near-duplicate checks",
    "质检方式": "Quality checks", "逐轮评审 + 全段一致性评审": "Review each turn and check conversation consistency",
    "验证方式": "Verification", "受限 calculator 与录制 JSON 快照重放；其他工具轨迹隔离": "Replay bounded calculator calls and recorded JSON snapshots. Quarantine other tool traces.",
    "比较目标": "Comparison goals", "生成方式": "Generation method", "本地整数算术模板": "Local integer arithmetic templates",
    "受限 AST 逐步计算，不调用模型": "Check each arithmetic step locally. No model calls.",
    "质检证据": "Quality evidence", "逐条记录、失败原因、来源指纹与 SHA-256 清单": "Per-record evidence, failure reasons, source fingerprints, and SHA-256 manifest",
    "发布状态": "Release status", "自动检查候选，尚未完成人工审核": "Automatically checked candidates. Human review is pending.",
    "旧版默认配置 / 旧版默认配置": "Legacy default / Legacy default",
    "CPT 清洗去重": "CPT cleaning and deduplication", "Agent 轨迹重放验证": "Agent replay checks",
    "DPO / RLAIF / ORPO 偏好对齐": "DPO / RLAIF / ORPO preferences", "CoT 推理核对": "CoT reasoning checks",
    "GSM8K 算术验证": "GSM8K arithmetic checks", "多轮对话生成与一致性验证": "Multi-turn generation and consistency checks",
    "输入解析与来源追踪": "Input parsing and source tracking", "训练文件与质量报告": "Training files and quality reports",
    "生成候选，不重复导入记录": "Generate candidates. Keep imported records intact.",
    "放大工作流视图": "Expand workflow view", "选择任务": "Choose a task",
    "展开工作流画布与节点配置；任务列表可从“选择任务”打开。": "Expand the canvas and node panel. Open Choose a task to switch runs.",
    "可在工作流节点中选用": "Available in workflow nodes",
    "所选节点": "Selected node", "生成模型": "Generation model", "启用目标": "Enabled goals",
    "失败，可重试": "Failed, ready to retry", "已完成，部分目标需处理": "Finished with issues",
    "解析单元": "Parsed units", "可处理": "Ready", "输入隔离": "Quarantined inputs", "超出上限": "Beyond the limit",
    "SFT 生成与验证": "SFT generation and checks", "多轮对话生成与验证": "Multi-turn generation and checks",
    "CPT 清洗与去重": "CPT cleaning and deduplication", "偏好对生成与验证": "Preference generation and checks",
    "模型服务": "Model Services", "模型选择位置": "Model selection", "工作流节点": "Workflow nodes",
    "点击节点分别选择生成与评审模型": "Select generation and review models in each node",
    "点击工作流节点选择模型": "Choose models in workflow nodes",
    "登记服务地址、凭据与预算；具体模型在工作流节点中选择。": "Register service addresses, credentials, and budgets. Choose models in workflow nodes.",
    "候选样本规模": "Candidate count", "并发请求上限": "Concurrency limit", "每批候选数": "Candidates per batch",
    "设置单个生成目标的候选规模。质检后的实际导出数量可能较少；导入轨迹与 CPT 文档不会重复凑数。": "Set the candidate count for each generation goal. Quality checks may reduce exports. Imported traces and CPT documents are kept intact.",
    "限制来源解析后的处理范围。开放需求规划也受此上限约束。": "Limit the number of source units to process. This also limits open-brief planning.",
    "同一节点内同时处理的样本数。可按模型服务的限流调低；阶段仍按数据依赖顺序执行。": "Limit samples processed at once within a node. Lower this value to meet service limits. Stages run in order.",
    "只将当前批次送入执行队列，完成后再读取下一批；每条结果单独保存断点。": "Queue one batch at a time. Save a checkpoint for each result before loading the next batch.",
    "支持数万条候选。分批规划、增量统计；失败后可从逐条断点继续。": "Plan and process large runs in batches. Track progress as results arrive. Resume from saved checkpoints after a failure.",
    "工作流节点配置": "Configure workflow nodes", "直接点击节点，在右侧选择该步骤的模型。": "Select a node. Choose its models in the panel on the right.",
    "点击配置节点": "Select to configure", "独立质量评审模型": "Independent review model",
    "此节点使用本地规则，不需要配置模型。": "This node uses local rules. No model is needed.",
    "请先在模型服务中登记服务地址与凭据，再回到节点选择模型。": "Register an address and credentials in Model Services. Then choose a model in this node.",
    "选择或输入模型名": "Choose or enter a model name",
    "每个节点独立保存选择；开始运行后，本次配置固定。": "Each node keeps its own model choices. The configuration is fixed when the run starts.",
    "输入解析保留来源位置；开放需求按每批最多 50 个任务规划。": "Keep source locations during parsing. Plan open briefs in batches of up to 50 tasks.",
    "只打包通过质量检查的记录，并附带来源与审核证据。": "Package records that pass quality checks. Include source and review evidence.",
    "请为所有需要模型的节点选择服务与模型。": "Choose a service and model for every node that needs one.",
    "加载样本预览": "Load sample preview", "加载失败轨迹": "Load failed traces", "加载隔离记录": "Load quarantined records",
    "最多显示前 100 条；完整记录保留在本次产物中。": "Show the first 100 records. The full records are saved with this run.",
    "候选 / 分钟": "Candidates / minute", "旧版默认配置": "Legacy default",
})


def translate(value: Any, language: str = "en") -> Any:
    """Translate an exact interface phrase and leave all other values intact."""
    if language_code(language) != "en" or not isinstance(value, str):
        return value
    if value in ZH_EN:
        return ZH_EN[value]
    match = re.fullmatch(r"([\d,]+) 轮 · ([\d,]+) 条消息", value)
    if match:
        return f"{match.group(1)} turns · {match.group(2)} messages"
    match = re.fullmatch(r"共 ([\d,]+) 条消息", value)
    if match:
        return f"{match.group(1)} messages"
    match = re.fullmatch(r"指纹 ([a-f0-9]+)", value)
    if match:
        return f"Fingerprint {match.group(1)}"
    match = re.fullmatch(r"已审核 ([\d,]+) 条 · 待处理 ([\d,]+) 条", value)
    if match:
        return f"Reviewed {match.group(1)} · Pending {match.group(2)}"
    match = re.fullmatch(r"(.+) · ([\d,]+) (条|对) · ([a-f0-9]{8})", value)
    if match:
        unit = "pairs" if match.group(3) == "对" else "examples"
        return f"{match.group(1)} · {match.group(2)} {unit} · {match.group(4)}"
    match = re.fullmatch(r"共 (\d+) 条", value)
    if match:
        return f"{match.group(1)} runs"
    match = re.fullmatch(r"显示 (\d+) / 匹配 (\d+)", value)
    if match:
        return f"Showing {match.group(1)} / {match.group(2)} matches"
    match = re.fullmatch(r"最近 (\d+) 条", value)
    if match:
        return f"Last {match.group(1)} events"
    match = re.fullmatch(r"(.+) 北京时间", value)
    if match:
        return f"{match.group(1)} UTC+8"
    match = re.fullmatch(r"通过 / (\d+) 候选 · (\d+) 条需处理", value)
    if match:
        return f"passed / {match.group(1)} candidates · {match.group(2)} to review"
    match = re.fullmatch(r"第 (\d+) 次运行", value)
    if match:
        return f"Run attempt {match.group(1)}"
    match = re.fullmatch(r"更新于 (.+) UTC", value)
    if match:
        return f"Updated {match.group(1)} UTC"
    match = re.fullmatch(r"(.+) · (待启动|等待|执行中|完成|失败，可重试|已停止|已完成，部分目标需处理) · ([a-f0-9]{8})", value)
    if match:
        return f"{translate(match.group(1), language)} · {translate(match.group(2), language)} · {match.group(3)}"
    match = re.fullmatch(r"运行失败：(.+)。已完成的步骤与模型响应已保存。", value)
    if match:
        return f"Run failed: {match.group(1)}. Completed steps and model responses are saved."
    match = re.fullmatch(r"当前筛选：(.+) · 最近 (\d+) 条事件", value)
    if match:
        return f"Current filter: {translate(match.group(1), language)} · Last {match.group(2)} events"
    match = re.fullmatch(r"([\d,]+) / ([\d,]+) 单元", value)
    if match:
        return f"{match.group(1)} / {match.group(2)} units"
    match = re.fullmatch(r"(等待|执行中|已完成|失败|已停止|已跳过) · (\d+)%", value)
    if match:
        return f"{translate(match.group(1), language)} · {match.group(2)}%"
    match = re.fullmatch(r"批次 ([\d,]+) / ([\d,]+)", value)
    if match:
        return f"Batch {match.group(1)} / {match.group(2)}"
    match = re.fullmatch(r"预计剩余 ([\d,]+) 分钟", value)
    if match:
        return f"About {match.group(1)} minutes remaining"
    match = re.fullmatch(r"(.+) · 已选 (\d+) 类目标 · 候选规模 ([\d,]+) · 并发 (\d+) · 每批 (\d+)", value)
    if match:
        return f"{translate_label(match.group(1), language)} · {match.group(2)} goal types · {match.group(3)} candidates · {match.group(4)} concurrent · {match.group(5)} per batch"
    remaining_budget = re.fullmatch(r"剩余额度\s*\$([\d,.]+)", value)
    if remaining_budget:
        return f"Remaining budget ${remaining_budget.group(1)}"
    reset_budget = re.fullmatch(r"预算已清零（原已用\s*\$([\d,.]+)，已记审计）", value)
    if reset_budget:
        return f"Budget reset (previous usage ${reset_budget.group(1)}; audit recorded)"
    approval_count = re.fullmatch(r"已确认\s*(\d+)\s*/\s*(\d+)", value)
    if approval_count:
        return f"{approval_count.group(1)} / {approval_count.group(2)} approved"
    approved_at = re.fullmatch(r"确认时间\s*(.+)", value)
    if approved_at:
        return f"Approved at {approved_at.group(1)}"
    match = re.fullmatch(r"([\d,]+) 个匹配文件 · 来源优先", value)
    if match:
        return f"{match.group(1)} matching files · sources first"
    match = re.fullmatch(r"为保持浏览流畅，当前显示前\s*(\d+)\s*个文件；请使用搜索缩小范围。", value)
    if match:
        return f"Showing the first {match.group(1)} files. Search to narrow the results."
    match = re.fullmatch(r"([\d,]+) 个阶段 · ([\d,]+) 条数据依赖", value)
    if match:
        return f"{match.group(1)} stages · {match.group(2)} data dependencies"
    match = re.fullmatch(r"已选目标\s*(\d+)", value)
    if match:
        return f"Selected goals: {match.group(1)}"
    match = re.fullmatch(r"(.+) · (\d+) 项已选", value)
    if match:
        return f"{translate_label(match.group(1), language)} · {match.group(2)} selected"
    match = re.fullmatch(r"(.+) · 未选", value)
    if match:
        return f"{translate_label(match.group(1), language)} · not selected"
    match = re.fullmatch(r"(.+) · 已选 (\d+) 类目标 · 最多处理 ([\d,]+) 单元", value)
    if match:
        return f"{translate_label(match.group(1), language)} · {match.group(2)} goal types · up to {match.group(3)} units"
    match = re.fullmatch(r"本次来源类型[：:]\s*(.+)", value)
    if match:
        return f"Source type: {translate_label(match.group(1), language)}"
    match = re.fullmatch(r"当前未达到放量条件：(.+)", value)
    if match:
        reasons = [translate_label(reason.strip(), language) for reason in match.group(1).split("、")]
        return "Release checks not met: " + ", ".join(reasons)
    match = re.fullmatch(
        r"(文档清洗、分块与去重|SFT、多轮与 Agent 轨迹|ORPO、DPO 与 RLAIF|CoT 与算术核验)。点击(清空|启用)整组；下方可逐项调整。",
        value,
    )
    if match:
        descriptions = {
            "文档清洗、分块与去重": "Clean, split, and deduplicate documents",
            "SFT、多轮与 Agent 轨迹": "SFT, multi-turn, and agent traces",
            "ORPO、DPO 与 RLAIF": "ORPO, DPO, and RLAIF",
            "CoT 与算术核验": "CoT and checked arithmetic",
        }
        action = "clear" if match.group(2) == "清空" else "enable"
        return f"{descriptions[match.group(1)]}. Click to {action} this group. Adjust individual goals below."
    match = re.fullmatch(r"上限\s*\$([\d,.]+)", value)
    if match:
        return f"Limit ${match.group(1)}"
    match = re.fullmatch(r"当前默认：\s*(.+)", value)
    if match:
        return f"Current default: {match.group(1)}"
    match = re.fullmatch(r"([\d,]+) 个端点", value)
    if match:
        return f"{match.group(1)} endpoints"
    match = re.fullmatch(r"(\d+) 个槽位", value)
    if match:
        return f"{match.group(1)} slots"
    match = re.fullmatch(r"(.+?)等\s*([\d,]+)\s*个角色", value)
    if match:
        return f"{translate_label(match.group(1), language)} and {match.group(2)} roles"
    match = re.fullmatch(r"(?:历史导出发布|人工审核发布)\s*·\s*(.+)", value)
    if match:
        prefix = "Previous export" if value.startswith("历史导出") else "Reviewed release"
        return f"{prefix} · {match.group(1)}"
    match = re.fullmatch(r"(?:历史导出|人工审核)\s*·\s*(.+)", value)
    if match:
        prefix = "Previous export" if value.startswith("历史导出") else "Reviewed release"
        return f"{prefix} · {match.group(1)}"
    match = re.fullmatch(r"创建于\s*(.+)", value)
    if match:
        return f"Created {match.group(1)}"
    match = re.fullmatch(r"更多参数（(\d+) 项）", value)
    if match:
        return f"More settings ({match.group(1)})"
    match = re.fullmatch(r"第\s*(\d+)\s*/\s*(\d+)\s*页 ·\s*(\d+)\s*(条|对)", value)
    if match:
        unit = "pairs" if match.group(4) == "对" else "items"
        return f"Page {match.group(1)} of {match.group(2)} · {match.group(3)} {unit}"
    match = re.fullmatch(r"([\d,]+) 条", value)
    if match:
        return f"{match.group(1)} items"
    match = re.fullmatch(r"(?:默认|推理与反思|长上下文|工具使用|双语桥接)\s+(\d+(?:\.\d+)?%)", value)
    if match:
        label = value.rsplit(None, 1)[0]
        return f"{translate_label(label, language)} {match.group(1)}"
    role_parts = value.split("、")
    if len(role_parts) > 1 and all(part in ZH_EN for part in role_parts):
        return ", ".join(ZH_EN[part] for part in role_parts)
    match = re.fullmatch(r"查看完整数据依赖\s*·\s*(\d+)\s*条", value)
    if match:
        return f"View data dependencies · {match.group(1)} links"
    match = re.fullmatch(r"当前文件共\s*([\d,]+)\s*条 · 正在查看第\s*([\d,]+)\s*条", value)
    if match:
        return f"{match.group(1)} examples in this file · viewing {match.group(2)}"
    match = re.fullmatch(r"本目标共\s*([\d,]+)\s*条 · 当前展示前\s*([\d,]+)\s*条中的第\s*([\d,]+)\s*条", value)
    if match:
        return f"{match.group(1)} examples for this goal · viewing {match.group(3)} of the first {match.group(2)}"
    match = re.fullmatch(r"第\s*(\d+)\s*/\s*(\d+)\s*条", value)
    if match:
        return f"Example {match.group(1)} of {match.group(2)}"
    match = re.fullmatch(r"([\d,]+) 个步骤 · ([\d,]+) 条消息", value)
    if match:
        return f"{match.group(1)} steps · {match.group(2)} messages"
    match = re.fullmatch(r"(.+)\s*·\s*(\d+) 条消息", value)
    if match:
        return f"{translate_label(match.group(1).strip(), language)} · {match.group(2)} messages"
    match = re.fullmatch(r"([\d,]+) 条消息", value)
    if match:
        return f"{match.group(1)} messages"
    match = re.fullmatch(r"([\d,]+) 个工具定义", value)
    if match:
        return f"{match.group(1)} tool definitions"
    match = re.fullmatch(r"([\d,]+) 轮模型评估记录", value)
    if match:
        return f"{match.group(1)} model review records"
    match = re.fullmatch(r"([\d,]+) 轮 · ([\d,]+) 条消息", value)
    if match:
        return f"{match.group(1)} turns · {match.group(2)} messages"
    match = re.fullmatch(r"候选\s*(\d+)\s*·\s*排名\s*(.+)", value)
    if match:
        return f"Candidate {match.group(1)} · Rank {match.group(2)}"
    match = re.fullmatch(r"([\d,]+) 次调用已核对", value)
    if match:
        return f"{match.group(1)} calls verified"
    match = re.fullmatch(r"([\d,]+) 组重复调用已剪枝", value)
    if match:
        return f"{match.group(1)} duplicate call groups pruned"
    match = re.fullmatch(r"([\d,]+) 轮回答已核对", value)
    if match:
        return f"{match.group(1)} turns verified"
    match = re.fullmatch(r"第\s*(\d+)\s*轮对话", value)
    if match:
        return f"Turn {match.group(1)}"
    match = re.fullmatch(r"第\s*(\d+)\s*条消息（索引\s*(\d+)）", value)
    if match:
        return f"Message {match.group(1)} (index {match.group(2)})"
    match = re.fullmatch(
        r"第\s*(\d+)\s*条消息（索引\s*(\d+)）\s*·\s*来源：(.+)", value
    )
    if match:
        return f"Message {match.group(1)} (index {match.group(2)}) · Source: {match.group(3)}"
    match = re.fullmatch(r"展开后续\s*(\d+)\s*轮对话", value)
    if match:
        return f"Show {match.group(1)} later turns"
    match = re.fullmatch(r"展开工具输出（([\d,]+) 字）", value)
    if match:
        return f"Expand tool output ({match.group(1)} characters)"
    match = re.fullmatch(r"(?:🧠\s*)?思考（([\d,]+) 字）", value)
    if match:
        return f"🧠 Reasoning ({match.group(1)} characters)"
    match = re.fullmatch(r"已省略中间\s*([\d,]+)\s*条消息", value)
    if match:
        return f"Omitted {match.group(1)} middle messages"
    match = re.fullmatch(r"来源：\s*(.+)", value)
    if match:
        return f"Source: {match.group(1)}"
    match = re.fullmatch(r"校验记录：\s*(.+)", value)
    if match:
        return f"Verification record: {match.group(1)}"
    match = re.fullmatch(r"其他消息\s*·\s*(.+)", value)
    if match:
        return f"Other message · {translate_label(match.group(1), language)}"
    match = re.fullmatch(r"([\d,]+) 轮用户交互", value)
    if match:
        return f"{match.group(1)} user turns"
    match = re.fullmatch(r"([\d,]+) 次工具调用", value)
    if match:
        return f"{match.group(1)} tool calls"
    match = re.fullmatch(r"([\d,]+) 个可审任务 · 候选样本", value)
    if match:
        return f"{match.group(1)} reviewable tasks · candidate examples"
    match = re.fullmatch(r"有效审核覆盖\s*(\d+(?:\.\d+)?)%", value)
    if match:
        return f"Verified review coverage {match.group(1)}%"
    match = re.fullmatch(r"显示最近\s*(\d+)\s*/\s*(\d+)\s*条", value)
    if match:
        return f"Showing the latest {match.group(1)} of {match.group(2)}"
    match = re.fullmatch(r"当前工作区找到\s*(\d+)\s*个版本目录 ·\s*(\d+)\s*个版本通过文件校验", value)
    if match:
        return f"Found {match.group(1)} release folders · {match.group(2)} passed file checks"
    match = re.fullmatch(r"生成成对回答并通过质量检查后，(DPO|ORPO) 候选会显示在这里供人工比较。", value)
    if match:
        return f"{match.group(1)} candidates appear here for review after paired answers pass quality checks."
    match = re.fullmatch(
        r"当前工作区没有通过产物校验的 (CPT|SFT|DPO|ORPO) 工作流。先在“自动工作流”生成 \1 候选，再进入人工审核。",
        value,
    )
    if match:
        target = match.group(1)
        return f"No checked {target} workflow is available. Create {target} candidates in Workflows, then review them here."
    match = re.fullmatch(r"文件：(.+?) · 本报告只说明结构与有效审核证据，不代表事实正确性。", value)
    if match:
        return f"File: {match.group(1)} · This report covers structure and linked review evidence, not factual accuracy."
    match = re.fullmatch(r"正文长度：最短\s*([\d,]+)\s*字符 · 平均\s*([\d,.]+)\s*字符 · 最长\s*([\d,]+)\s*字符", value)
    if match:
        return f"Text length: min {match.group(1)} characters · average {match.group(2)} · max {match.group(3)} characters"
    match = re.fullmatch(r"匹配\s*(\d+)\s*/\s*(\d+)\s*条问题；重复内容与重复 ID 为单独的汇总计数。", value)
    if match:
        return f"{match.group(1)} of {match.group(2)} issues match. Duplicate content and IDs are counted separately."
    match = re.fullmatch(r"(?:来源|产物) / (.+)", value)
    if match:
        prefix = "Source" if value.startswith("来源") else "Result"
        return f"{prefix} / {match.group(1)}"
    return value


def translate_label(value: Any, language: str = "en") -> Any:
    """Translate a control label, including a short icon prefix or suffix."""
    if language_code(language) != "en" or not isinstance(value, str):
        return value
    if value in ZH_EN:
        return ZH_EN[value]
    translated = translate(value, language)
    if translated != value:
        return translated
    remaining_files = re.fullmatch(r"还可在数据管理中查看其余\s*(\d+)\s*个文件。", value)
    if remaining_files:
        return f"View {remaining_files.group(1)} more files in Data Library."
    result = value
    for source, target in sorted(ZH_EN.items(), key=lambda pair: len(pair[0]), reverse=True):
        if source and source in result:
            result = result.replace(source, target)
    return result


def canonical_navigation_route(
    value: Any,
    routes: Any,
    icons: dict[str, str],
    fallback: str = "总览",
) -> str:
    """Resolve stored route keys and labels from older localized nav widgets."""
    route_list = tuple(routes)
    if value in route_list:
        return value

    label = " ".join(str(value).split())
    for route in route_list:
        icon = icons.get(route, "•")
        for language in ("zh", "en"):
            translated = translate_label(route, language)
            candidates = (route, translated, f"{icon} {route}", f"{icon} {translated}")
            if label in {" ".join(candidate.split()) for candidate in candidates}:
                return route
    return fallback


class _MarkupTextLocalizer(HTMLParser):
    """Translate exact visible text nodes while preserving markup and user values."""

    def __init__(self, language: str):
        super().__init__(convert_charrefs=False)
        self.language = language
        self.parts: list[str] = []
        self._preserved_tags: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        source = self.get_starttag_text()
        if self._preserved_tags:
            self.parts.append(source)
            if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
                self._preserved_tags.append(tag)
            return
        classes = next((set((value or "").split()) for name, value in attrs if name == "class"), set())
        if classes.intersection({"md", "df-artifact-body"}):
            self.parts.append(source)
            if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
                self._preserved_tags.append(tag)
            return
        if language_code(self.language) == "en":
            for name, value in attrs:
                if name not in {"aria-label", "title", "placeholder", "alt", "data-i18n-before"} or not value:
                    continue
                translated = translate(value, self.language)
                if translated == value:
                    continue
                attribute = re.compile(rf"(\s{re.escape(name)}\s*=\s*)([\"'])(.*?)(\2)", re.IGNORECASE)
                source = attribute.sub(
                    lambda match, text=translated: (
                        match.group(1) + match.group(2) + html.escape(text, quote=True) + match.group(4)
                    ),
                    source,
                    count=1,
                )
        self.parts.append(source)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.parts.append(self.get_starttag_text())

    def handle_endtag(self, tag: str) -> None:
        self.parts.append(f"</{tag}>")
        for index in range(len(self._preserved_tags) - 1, -1, -1):
            if self._preserved_tags[index] == tag:
                del self._preserved_tags[index:]
                break

    def handle_data(self, data: str) -> None:
        if self._preserved_tags:
            self.parts.append(data)
            return
        trimmed = data.strip()
        if not trimmed:
            self.parts.append(data)
            return
        translated = translate(trimmed, self.language)
        self.parts.append(data[:len(data) - len(data.lstrip())] + translated + data[len(data.rstrip()):])

    def handle_entityref(self, name: str) -> None:
        self.parts.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        self.parts.append(f"&#{name};")

    def handle_comment(self, data: str) -> None:
        self.parts.append(f"<!--{data}-->")

    def handle_decl(self, decl: str) -> None:
        self.parts.append(f"<!{decl}>")


def translate_markup(value: str, language: str = "en") -> str:
    if language_code(language) != "en" or not isinstance(value, str):
        return value
    parser = _MarkupTextLocalizer(language)
    parser.feed(value)
    parser.close()
    return "".join(parser.parts)


def _localized_dataframe(data: Any, language: str) -> Any:
    """Translate table headings while leaving cell values and source data intact."""
    if language_code(language) != "en":
        return data
    try:
        import pandas as pd
        if isinstance(data, pd.DataFrame):
            return data.rename(columns=lambda item: translate_label(item, language))
    except ImportError:
        pass
    if isinstance(data, list) and data and all(isinstance(row, dict) for row in data):
        return [
            {translate_label(key, language): value for key, value in row.items()}
            for row in data
        ]
    return data


def initialize_language(st: Any) -> None:
    """Read a shareable ``?lang=`` preference before rendering the interface."""
    requested = st.query_params.get("lang")
    if requested in {"en", "zh"}:
        st.session_state["ui_language"] = requested
    else:
        st.session_state.setdefault("ui_language", "zh")
    choice = "English" if language_code(st.session_state["ui_language"]) == "en" else "简体中文"
    st.session_state["ui-language-choice"] = choice


def set_language_from_choice(st: Any, choice: str) -> str:
    language = language_code(choice)
    st.session_state["ui_language"] = language
    st.query_params["lang"] = language
    return language


def install_streamlit_localization() -> None:
    """Translate static labels passed through Streamlit's shared renderer."""
    from streamlit.delta_generator import DeltaGenerator

    if getattr(DeltaGenerator, "_shujian_localization_installed", False):
        return

    label_methods = {
        "button", "download_button", "file_uploader", "selectbox", "multiselect",
        "radio", "segmented_control", "checkbox", "form_submit_button", "text_input", "text_area",
        "number_input", "date_input", "time_input", "expander", "popover",
        "slider", "toggle", "link_button", "page_link", "metric", "text", "caption", "progress",
        "title", "header", "subheader", "info", "success", "warning", "error",
        "exception", "toast", "dialog", "dataframe",
    }
    markup_methods = {"html", "markdown"}
    option_methods = {"selectbox", "multiselect", "radio", "segmented_control", "pills"}
    label_keywords = {"label", "help", "placeholder", "caption", "aria_label", "text"}
    methods = label_methods | markup_methods | {"write", "tabs"}

    def current_language() -> str:
        try:
            import streamlit as st
            return language_code(st.session_state.get("ui_language", "zh"))
        except Exception:
            return "zh"

    def translate_call(method_name: str, args: tuple[Any, ...], kwargs: dict[str, Any]):
        language = current_language()
        if language != "en":
            return args, kwargs

        translated_args = list(args)
        if method_name == "dataframe" and translated_args:
            translated_args[0] = _localized_dataframe(translated_args[0], language)
        elif translated_args and method_name in label_methods - {"dataframe"}:
            translated_args[0] = translate_label(translated_args[0], language)
        elif translated_args and method_name in markup_methods:
            translated_args[0] = translate_markup(translated_args[0], language)
        elif method_name == "write":
            translated_args = [translate(item, language) if isinstance(item, str) else item for item in translated_args]
        elif method_name == "tabs" and translated_args and isinstance(translated_args[0], (list, tuple)):
            translated_args[0] = [translate_label(item, language) for item in translated_args[0]]

        translated_kwargs = dict(kwargs)
        for key in label_keywords:
            if isinstance(translated_kwargs.get(key), str):
                translated_kwargs[key] = translate_label(translated_kwargs[key], language)

        if method_name in option_methods:
            positional_formatter = len(translated_args) > 3 and method_name in {"selectbox", "multiselect", "radio"}
            formatter = translated_args[3] if positional_formatter else translated_kwargs.get("format_func")
            if callable(formatter):
                mapper = translate_label if method_name == "radio" else translate
                wrapped_formatter = lambda item, fn=formatter, map_value=mapper: map_value(fn(item), language)
            else:
                wrapped_formatter = lambda item: translate(item, language)
            if positional_formatter:
                translated_args[3] = wrapped_formatter
            else:
                translated_kwargs["format_func"] = wrapped_formatter

        return tuple(translated_args), translated_kwargs

    import streamlit as st

    for target in (DeltaGenerator, st):
        for name in sorted(methods):
            original = getattr(target, name, None)
            if original is None or getattr(original, "_shujian_i18n_wrapper", False):
                continue

            def make_wrapper(method_name: str, method: Any, is_class_method: bool):
                if is_class_method:
                    @wraps(method)
                    def localized(self: Any, *args: Any, **kwargs: Any):
                        args, kwargs = translate_call(method_name, args, kwargs)
                        return method(self, *args, **kwargs)
                else:
                    @wraps(method)
                    def localized(*args: Any, **kwargs: Any):
                        args, kwargs = translate_call(method_name, args, kwargs)
                        return method(*args, **kwargs)
                localized._shujian_i18n_wrapper = True
                return localized

            setattr(target, name, make_wrapper(name, original, target is DeltaGenerator))
