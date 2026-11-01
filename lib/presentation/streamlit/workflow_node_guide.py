"""Explain each workflow node beside its controls, without changing its draft."""
from __future__ import annotations

import streamlit as st

from lib.presentation.streamlit.i18n import UntranslatedText


# A section carries its heading and body in both supported display languages.
# Keep operational guidance here; validation and execution belong to the domains.
_MODEL_CAPACITY = (
    "容量与共享并发", "Capacity and shared concurrency",
    "在本节点的模型设置中填写上下文容量、单次输出上限和最大并发。上下文需要容纳指令、资料与历史；输出上限约束本次可返回的内容，部分服务会把原生思考也计入其中。同一模型被多个节点或任务复用时共享并发额度，不会各自获得一份额度。",
    "Set context capacity, maximum output tokens and concurrency in this node's model settings. Context must fit instructions, sources and history. The output limit caps the response; some providers also count native reasoning toward it. Nodes and tasks that reuse the same model share its concurrency allowance.",
)
_THINKING_EFFORT = (
    "思考强度与输出篇幅", "Thinking effort and response length",
    "思考强度保留为平台的 low、medium、high、xhigh、max，发请求时才映射到上游支持的设置。不同服务未必支持全部档位；自动模式不强加档位。强度、输出篇幅与 token 容量是不同设置。“verbosity 5”只是观察到的自述，不能据此确认上游配置，也不能把它当成 high 或 max。",
    "The platform stores low, medium, high, xhigh or max, then maps it to the provider's supported settings when sending a request. Providers may support fewer levels. Auto leaves the choice to the provider. Effort, response length and token capacity are separate. A phrase such as “verbosity 5” is an observed self-description, not proof of a provider setting or high/max effort.",
)
_OUTPUT_ADAPTATION = (
    "控制文本匹配与替换", "Match and replace control text",
    "在本节点「输出适配」中开关整套规则、修改匹配原文和目标文本，先用预览检查。它按明确字符串标记控制文本，再在训练读取时按目标配置替换；不调用模型，不改原始推理存档。关闭后保留原文，不会关闭模型思考。仅替换已确认的控制字段，不用它修正事实或删掉推理。",
    "Use this node's Output rules tab to enable rules, edit exact text and set a replacement. Check the preview first. Rules mark known control text and adapt it when training data is read. They make no model calls and preserve the raw reasoning archive. Disabling them keeps the text; it does not disable thinking. Use them for confirmed control fields, not factual repairs or removing reasoning.",
)
_PROMPT_EDITING = (
    "提示词与个人模板", "Prompts and personal templates",
    "在本节点「提示词与风格」修改实际使用的指令，可恢复默认或保存个人模板。按业务需要描述目标、可用信息与边界，不必规定固定思考步骤。新任务冻结当时的配置；修改草稿不会热改正在运行的任务。",
    "Edit the actual instructions in this node's Prompts and style tab. Restore defaults or save a personal template there. Describe the goal, available information and boundaries without forcing a fixed reasoning sequence. New runs freeze their configuration; editing a draft does not change an active run.",
)

_GUIDES = {
    "ingest": (
        (
            "先选择资料的读取方式", "Choose how to read sources",
            "可导入 PDF、DOCX、Markdown、TXT、LaTeX／TeX 等资料。本地文本解析适合已有文字层的文档；模型辅助解析整理提取后的文字；Docling 处理本地版面、表格与公式。扫描页和图片需要图片识别能力，单纯提取文字读不到图中内容。",
            "Import sources such as PDF, DOCX, Markdown, TXT and LaTeX/TeX. Local text parsing reads existing text layers. Model-assisted parsing organizes extracted text. Docling handles local layout, tables and formulas. Scans and images need image recognition; text extraction alone cannot read their visual content.",
        ),
        (
            "多模态与原生 PDF 能力", "Image and native PDF support",
            "在节点模型设置中探测并确认图片、原生 PDF 与工具能力；三者并不等价。当前多模态解析按页或图片块识别，Docling 也可选模型辅助。不要仅凭模型名称启用能力；探测成功只说明该测试请求可用，复杂公式与图表仍要看实际解析预览。",
            "Probe and confirm image, native PDF and tool support in the node's model settings. These are separate capabilities. Multimodal parsing reads pages or image regions; Docling can also use model assistance. A model name is not proof of support. A successful probe confirms that test request, not accurate recognition of every formula or chart.",
        ),
        (
            "DSH 会话与缺失推理", "DSH sessions and missing reasoning",
            "手动导入会话数据库使用 DSH 格式，也可用入口支持的标准 JSON／JSONL。缺失推理可选「保留原样」「仅补缺失推理」「排除缺失记录」。补写仍是待完善的实验功能：生成的是解释正文，不是恢复真实思考；已有推理与原答案保留。排除策略会跳过整段缺失会话，不拆坏多轮记录。",
            "Manual session-database import uses the DSH format. The input also accepts supported standard JSON/JSONL. Choose Keep as is, Fill missing reasoning or Exclude missing records. Filling is experimental: it creates an explanation, not a recovered original thought process. Existing reasoning and answers remain. Exclusion skips the whole affected conversation rather than breaking its turns apart.",
        ),
        (
            "复用资料与 RAG", "Reuse sources and retrieve knowledge",
            "已解析资料保存在可复用资料库，下次工作可直接选择。知识库检索支持本地持久库与 Qdrant：向量用于查找相关资料，送给生成模型的是检索出的文本，不是向量本身。阅读任务所需的资料必须保留在训练输入中，内部检索包装与向量不必交付给训练模型。",
            "Parsed sources are saved for reuse in later work. Knowledge retrieval supports a persistent local store and Qdrant. Vectors find relevant sources; the generation model receives retrieved text, not the vectors themselves. Keep evidence required by a reading task in its training input. Internal retrieval wrappers and vectors need not be delivered to the training model.",
        ),
        (
            "解析不完整时", "When parsing is incomplete",
            "先看来源预览中的页码、表格、公式和图片，再调整本节点解析方式。扫描页无文字时改用已确认图片能力的模型；Docling 启动失败先看依赖与本地模型准备状态；DSH 导入失败先看格式诊断。不要先让下游生成节点补猜缺失资料。",
            "Check page numbers, tables, formulas and images in the source preview before changing this node's parser. For scans with no text, select a model with confirmed image support. If Docling cannot start, check its dependencies and local model setup. For DSH failures, read the format diagnosis. Do not ask downstream generation to guess missing source content.",
        ),
    ),
    "director": (
        (
            "设计真实用户发言", "Design realistic user turns",
            "指导员决定情景、知识范围与对话方向，提问角色负责说出用户的话，回答角色再完成任务。新生成问答必须经过指导员；完整导入会话和仅制作 CPT 的流程可绕过。两种用户表达方式可随情景变化，不把同一用户固定成长句或短句。",
            "The director selects the situation, knowledge scope and dialogue direction. A question role voices the user, then an answer role handles the task. Newly generated dialogue requires the director. Complete imported conversations and CPT-only work may bypass it. User-expression modes can vary with the situation without fixing a person to long or short sentences.",
        ),
        (
            "知识应用还是资料阅读", "Knowledge application or source reading",
            "在节点设置中选择「运用已学知识」「阅读用户资料」或兼顾两种能力，并选择用户语言和线索类型。指导员为下游指定清洗资料范围；无线索问题不能依赖训练用户看不见的“上文”，阅读问题则需要带上解决问题所需的材料。",
            "Choose knowledge application, document reading or a mix, then set user languages and clue types. The director assigns cleaned source ranges to downstream generation. A closed-book question must not depend on invisible prior text. A reading question must include the evidence needed to solve it.",
        ),
        (
            "上下文、工具与重复控制", "Context, tools and repetition",
            "持续上下文用于保留已有问题与覆盖情况，可按需要启用工具辅助调度。指导员和提问模型可以复用同一个模型，但各自使用独立角色上下文，共享模型并发。目标数量不应代替质量；资料已耗尽时先补资料或换场景，不靠重复话术填满数量。",
            "Persistent context retains prior questions and coverage. Tool-assisted planning can be enabled when needed. The director and question role can share a model, but use separate role contexts and shared concurrency. A sample target does not replace quality: when sources are exhausted, add sources or change the situation instead of filling the count with repeated phrasing.",
        ),
        _PROMPT_EDITING,
        (
            "用户发言不自然时", "When user turns feel unnatural",
            "先看问题计划和实际发包中谁是用户、谁是指导员，再调整本节点的情景与提问规则。若把示例偏好当成用户要求，检查规则和历史的来源；若频繁缺资料，检查指定范围和下游上下文容量。不要用固定句式掩盖角色或来源错误。",
            "Inspect the question plan and the actual user/director roles in the request before editing this node's scene and question rules. If an example preference becomes a user constraint, check its source in the rules and history. If evidence is missing, check source ranges and downstream context capacity. Fixed phrases do not resolve role or source errors.",
        ),
    ),
    "cpt": (
        (
            "清洗语料并供下游复用", "Clean a corpus for reuse",
            "CPT 语料处理整理排版、页眉和重复内容，保留原有知识、数字、单位、公式与表格关系。清洗合格正文可导出为 CPT，也可直接供指导员、SFT 和多轮使用；不必为了复用再解析一遍。这里制作训练语料，不在此启动模型训练。",
            "CPT processing cleans layout, headers and repetition while retaining knowledge, numbers, units, formulas and table relationships. Accepted text can be exported as a CPT corpus or reused by the director, SFT and multi-turn generation without parsing again. This node prepares training data; it does not train a model.",
        ),
        (
            "模型清洗或仅本地检查", "Model cleaning or local checks",
            "在「CPT 清洗方式」选择模型清洗与评审，或仅本地检查。模型模式会清洗并对照原文核对；本地模式保留原文，只做既有结构与去重等检查，不调用清洗模型。开放需求没有可对照的原文，合成语料不能宣称已完成原页保真核对。",
            "Choose model cleaning and review, or local checks only. Model mode cleans and compares against the source. Local mode preserves the text and runs existing structural and duplicate checks without a cleaning-model call. Open-ended requests have no source page, so synthetic text cannot claim source-page fidelity.",
        ),
        (
            "对照原文还是原页", "Compare source text or page images",
            "文字资料可对照原文；公式、图表较多时可选择「多模态对照原页」，并确认核对模型的图片能力。这是清洗后的保真核对，与问答的评分修正不是同一环节。CPT 正文按自身检查进入打包，不强制绕到 SFT 的答案返工。",
            "Use source-text comparison for text documents. For formulas and charts, select multimodal page comparison and confirm the checking model's image support. This checks cleaning fidelity; it is separate from scoring and repairing answers. CPT text follows its own checks to packaging rather than entering the SFT answer-repair loop.",
        ),
        _PROMPT_EDITING,
        (
            "清洗后丢信息时", "When cleaning loses information",
            "对照报告里的原文、清洗稿和核对依据：若原文就缺公式，回到输入解析；若清洗稿改错单位或删掉条件，调整本节点清洗提示词或原文核对方式。先修资料，再继续下游生成。",
            "Compare the source, cleaned text and review evidence in the report. If the source already lacks a formula, return to Input parsing. If cleaning changes a unit or removes a condition, adjust this node's cleaning prompt or comparison mode. Repair the source before continuing downstream generation.",
        ),
    ),
    "sft": (
        (
            "同次生成推理与答案", "Generate reasoning and answer together",
            "在「回答生成方式」选择原生 CoT 时，回答模型在同一次响应返回原生推理和答案；「提示词与风格」引导这次思考。普通生成及历史正文 CoT 仍按所选方式执行，不会因为打开页面被升级。风格化指令也不能保证模型每次都写出同样的思考过程。",
            "With native CoT selected under Answer generation mode, the answering model returns native reasoning and an answer in one response. Prompts and style guides that response. Ordinary generation and historical body-authored CoT keep their selected behavior; opening this page does not migrate them. Style instructions cannot guarantee the same reasoning behavior on every call.",
        ),
        _THINKING_EFFORT,
        _MODEL_CAPACITY,
        _OUTPUT_ADAPTATION,
        (
            "资料与最终训练输入", "Sources and the final training input",
            "回答模型使用指导员分配的清洗资料；最终训练输入按知识应用或资料阅读组织。知识应用样本应能独立提问，阅读样本需要包含必要资料。SFT 可以分字段保留推理，生成完成不等于已经质量通过，核查与隔离状态以报告为准。",
            "The answer model uses cleaned sources assigned by the director. Training input is built for knowledge application or source reading. Knowledge questions must stand alone; reading examples need the required source text. SFT can preserve reasoning in a separate field. A completed response is not a quality pass; check the report's review and isolation status.",
        ),
        (
            "截断、缺资料或规则不命中时", "Truncation, missing sources or unmatched rules",
            "响应被截断先看调用记录的停止原因和输出上限；资料不足先看指导员范围与上下文容量；控制文本未被替换先看「输出适配」预览是否逐字命中。检查后只调整对应设置，不为文本匹配问题重复购买生成。",
            "For truncation, inspect the call's stop reason and output limit. For missing evidence, inspect the director's source range and context capacity. For unchanged control text, check exact matches in Output rules preview. Adjust the relevant setting; text matching alone does not require another generation call.",
        ),
    ),
    "multiturn": (
        (
            "按真实历史逐轮继续", "Continue from the actual dialogue history",
            "指导员设计每轮用户发言，回答模型结合此前实际用户、助手和工具消息继续。用户陈述、助手猜测和工具观察不能互相冒充；历史助手的错误也不是既定事实。资料阅读或知识应用、用户语言在指导员节点配置。",
            "The director designs each user turn. The answer model continues from the actual user, assistant and tool messages. A user statement, assistant guess and tool observation are different evidence sources. An earlier assistant error is not an established fact. Set reading versus knowledge application and user languages on the director node.",
        ),
        (
            "每轮的原生 CoT", "Native CoT for each turn",
            "选择原生 CoT 后，每轮由同一个回答模型同次返回推理和答案。在本节点设置风格与思考要点，不会改动 SFT 节点的设置。普通生成和旧任务仍遵循保存的方式；导入的完整会话保留原始顺序与角色。",
            "With native CoT enabled, the same answering model returns reasoning and an answer for each turn. Set this node's style and thinking guidance here without changing the SFT node. Ordinary generation and older runs retain their saved mode. Complete imported conversations keep their original order and roles.",
        ),
        _THINKING_EFFORT,
        _MODEL_CAPACITY,
        _OUTPUT_ADAPTATION,
        (
            "重复、角色错位或中断时", "Repetition, role confusion or interruption",
            "先在报告展开完整对话，核对本轮之前实际可见的历史，再回指导员调整情景和发言方式。历史过长先检查上下文容量；中断先在任务管理查看检查点并恢复，不重建整段。角色问题应修历史归属或指令，不能靠删推理解决。",
            "Expand the full conversation in the report and check the history visible before the affected turn. Then adjust the director's scene or user-expression settings. For long history, check context capacity. After interruption, inspect the checkpoint in Task management and resume instead of rebuilding the conversation. Fix role attribution or instructions rather than deleting reasoning.",
        ),
    ),
    "cot": (
        (
            "独立 CoT 与当前生成方式", "Standalone CoT and current generation modes",
            "新 SFT／多轮可以直接在回答节点生成原生推理，不必再串一个 CoT 节点。独立 CoT 保留历史流程：显式推理与答案、保真风格改写、推敲后定稿、原生前缀续写等方式的输入与调用成本不同，按节点所选方式执行。",
            "New SFT and multi-turn work can generate native reasoning in the answer node without a separate CoT stage. The standalone node supports older flows. Explicit reasoning and answers, faithful restyling, draft refinement and native-prefix continuation use different inputs and numbers of calls. The selected mode determines execution.",
        ),
        _PROMPT_EDITING,
        _THINKING_EFFORT,
        _MODEL_CAPACITY,
        _OUTPUT_ADAPTATION,
        (
            "没有新增推理或成本过高时", "No new reasoning or excessive calls",
            "先检查是否启用风格化生成，以及所选方式是在生成、改写还是核验已有推理。关闭风格化生成会保留原有选择，不代表已生成新思考。再查看实际调用记录和历史工作流依赖；需要同次回答时，在 SFT／多轮中选择原生 CoT，而不是叠加两个流程。",
            "Check whether styled generation is enabled and whether the selected mode generates, rewrites or checks existing reasoning. Disabling styled generation preserves settings but does not create new reasoning. Inspect actual calls and the saved workflow dependencies. For a single joint response, choose native CoT in SFT or multi-turn instead of stacking both flows.",
        ),
    ),
    "trim": (
        (
            "语义修剪的用途", "What semantic trimming does",
            "修剪模型按照提示词处理提示词泄漏、检索包装、重复分支等内容，同时尽量保留完成任务所需的事实与推理。它会调用模型，可能改变表述；适合需要理解上下文才能决定的修改，不能用来掩盖未解决的事实错误。",
            "A trimming model follows prompts to handle instruction leakage, retrieval wrappers and repetitive branches while retaining facts and reasoning needed for the task. It makes model calls and may change wording. Use it when an edit requires contextual understanding, not to hide unresolved factual errors.",
        ),
        (
            "选择预设或自己的模板", "Choose a preset or your own template",
            "在节点设置选择修剪方向，在「提示词与风格」编辑模板、恢复默认或保存个人版本。关闭这一节点就不做这次模型修剪；原始记录仍保留。用户要求的引用、资料阅读上下文和必要纠错解释不应被一律当作泄漏。",
            "Choose the trimming goal in node settings. Edit, restore or save a personal template under Prompts and style. Disabling this node skips its model-based trimming and preserves the original record. Required citations, reading context and useful factual corrections are not automatically leakage.",
        ),
        (
            "固定控制文本不用模型改写", "Fixed control text needs no model rewrite",
            "已确认的固定控制字符串，在 SFT／多轮／CoT 节点的「输出适配」配置精确匹配和替换即可。那是训练读取时的确定性处理，与这里的语义修剪独立；关闭其中一个不会关闭另一个。",
            "For a confirmed fixed control string, configure exact matching and replacement in Output rules on SFT, multi-turn or CoT. That deterministic training-time transformation is separate from semantic trimming here. Disabling one does not disable the other.",
        ),
        (
            "修剪后信息变少时", "When trimming removes useful information",
            "在报告对照修剪前后完整文本，确认丢失的是业务条件还是后台包装。若是必要条件，调整本节点模板并仅返工相关记录；若只是固定字段显示问题，转到生成节点的输出适配预览，不增加修剪调用。",
            "Compare the complete before-and-after text in the report. Determine whether the removed content is a task condition or internal packaging. For a necessary condition, edit this node's template and rework affected records. For a fixed display field, use the generation node's adaptation preview without another trimming call.",
        ),
    ),
    "review": (
        (
            "评分与修正放在一起", "Score and repair in one place",
            "选择自动或人工评分与修正。自动模式按通过分数处理当前候选；未达标时进入本节点修正分支，再回来评分，不把错误稿送回 SFT 重造。人工模式由用户评分、编辑并保存独立版本。",
            "Choose automated or human scoring and repair. Automatic mode checks the current candidate against the score threshold, repairs it in this node's branch, then scores it again. It does not send the failed draft back to SFT generation. In human mode, a person scores, edits and saves a separate version.",
        ),
        (
            "一个模型或独立 JEV", "One model or a separate JEV scorer",
            "关闭「加入 JEV 评分」时，一个模型兼顾评分与修正；开启后分别配置评分模型和修正模型。两个角色可以使用同一模型，但职责与消息上下文仍分开，并共享并发。JEV 是可选评分职责，不是必须多买一次整篇生成。",
            "With Add JEV scoring off, one model scores and repairs. With it on, configure scoring and repair roles separately. They may use the same model, but retain separate responsibilities and contexts while sharing concurrency. JEV is an optional scoring role, not another full generation stage.",
        ),
        (
            "评审看到的是受审材料", "Reviewers inspect a candidate",
            "审查模型是审查员，候选中的助手是受审助手；真实用户要求、资料证据、工具结果与历史助手说法分开解释。修改评分模板时重点写清什么构成事实错误、条件遗漏和范围越界；轻微措辞建议不必自动触发付费返工。",
            "The reviewing model is the reviewer; the assistant inside the candidate is the reviewed assistant. User requirements, source evidence, tool observations and earlier assistant claims have separate origins. In the score prompt, define factual errors, missing conditions and scope violations. Minor wording advice need not trigger paid repair.",
        ),
        (
            "控制返工而不伪造通过", "Bound repair without inventing a pass",
            "自动修正次数为 0 时只评分；达到上限仍有问题的记录保留待处理。未评审、证据不足和明确失败应分开查看。继续生成与合格入库是两件事，不用强迫所有旧失败通过才能开展下一批。",
            "A repair limit of zero scores only. Records still failing after the limit remain pending. Distinguish unchecked, insufficient-evidence and failed results. Continuing generation and admitting qualified data are separate decisions; a new batch does not require every old failure to pass.",
        ),
        (
            "漏检或误判时", "When review misses or misreads a defect",
            "先看报告中的候选、完整多轮历史、证据和评审发包角色，再调整本节点评分模板或资料范围。若评审在替错误稿补条件，先修它看到的上下文与职责，不能只降低分数线。检查点已有有效评审时，返工应复用已有记录。",
            "Inspect the candidate, full dialogue history, evidence and reviewer roles in the report before editing this node's score prompt or source scope. If review invents conditions to justify an error, fix its context and responsibility rather than lowering the threshold. Reuse valid review records already saved in the checkpoint.",
        ),
    ),
    "jev": (
        (
            "可选的独立评分职责", "An optional independent scoring role",
            "JEV 给候选提供质量判断；修正由评分与修正节点的修正模型执行。需要单独选择评判模型时启用。关闭 JEV 后仍可由评分与修正节点的同一模型兼顾两种职责，不等于关闭全部质量检查。",
            "JEV judges candidate quality. The repair model in Score and repair performs corrections. Enable it when you want a separately chosen judge. With JEV off, one model can still score and repair; this does not disable all quality checks.",
        ),
        (
            "模型与核查依据", "Model and evidence",
            "在节点模型设置选择评分模型，在提示词中定义可核验的标准和证据要求。它可以与生成模型同名、共享并发；模型相同不代表评审已独立证明事实正确。核查依据不足时保留待复核状态。",
            "Select the scoring model in node settings and define checkable criteria and evidence requirements in its prompt. It may be the same model used for generation and share concurrency. Using that model again is not independent proof of factual correctness. Keep insufficient-evidence cases pending.",
        ),
        (
            "评分异常时", "When a score looks wrong",
            "先看评分所用候选版本、证据与角色标识，再回评分与修正节点调整模板和处理方式。若显示未核查，不把它当作通过；需要补充证据时先补资料，不反复重试同一份评分请求。",
            "Check the candidate version, evidence and role labels used for the score, then adjust the prompt and handling in Score and repair. Unchecked is not passed. When evidence is missing, add it instead of repeatedly sending the same scoring request.",
        ),
    ),
    "preference": (
        (
            "DPO、ORPO 与 RLAIF", "DPO, ORPO and RLAIF",
            "DPO 与 ORPO 都使用同一问题下的偏好回答对，训练目标不同；RLAIF 强调偏好来自 AI 反馈。这里生成与审核偏好数据，不执行这些训练算法，也不把选择导出格式当作训练效果证明。",
            "DPO and ORPO both use preferred and rejected responses to the same prompt, but have different training objectives. RLAIF describes preferences derived from AI feedback. This node prepares and checks preference data; it does not run those training algorithms. An export format is not evidence of training gains.",
        ),
        (
            "负例来自哪里", "Where rejected responses come from",
            "可优先复用已确认未解决问题且推理有遗漏的失败稿；没有合适稿件时，按节点选择生成渐进跑题、相反结论等负例。真实失败稿保留完整内容，合成负例的短推理上限只作用于合成路径。不能仅因推理短就判断答案一定错误。",
            "The node can reuse confirmed failures that leave the task unsolved and omit reasoning. If no suitable draft exists, it can generate gradual topic drift or an opposing conclusion according to your selection. Real failures keep their full content. Short-reasoning limits apply only to synthetic negatives. Short reasoning alone does not prove an answer wrong.",
        ),
        _PROMPT_EDITING,
        (
            "正负配对与格式复用", "Pair validity and format reuse",
            "正负回答必须对应同一问题和可见条件，双方保留自己的推理与审核依据。同一偏好对用于 DPO 与 ORPO 是格式复用，不应仅因格式不同再次生成内容；重复样本与不同训练投影需区分。负例存在不代表正例已经合格。",
            "Both responses must answer the same question under the same visible conditions. Keep each response's reasoning and review evidence. Reusing a pair for DPO and ORPO is a format projection, not a reason to regenerate its content. Distinguish duplicate examples from different training projections. A rejected response does not prove its preferred partner is qualified.",
        ),
        (
            "负例不合理或被隔离时", "When a negative is unsuitable or isolated",
            "先看负例来源、问题是否一致和隔离原因，再调整负例类型或复用失败稿选项。正负标反应修配对与评审依据；只有文件格式不同则检查导出投影，不通过强行造差或删短真实推理来补分差。",
            "Check the negative's source, prompt alignment and isolation reason before changing its type or failure-reuse setting. Reversed labels need a pairing or evidence fix. If only the file format differs, inspect the export projection. Do not manufacture a score gap by worsening content or truncating real reasoning.",
        ),
    ),
    "agent": (
        (
            "保留任务与工具轨迹", "Preserve tasks and tool trajectories",
            "Agent 数据包括真实的用户、助手、工具调用和工具观察，不能只把它当作一段普通聊天。原工具调用与结果要能对应；中间失败和修正可以作为经验来源，但不能把未成功的过程标成已完成任务。",
            "Agent data includes user and assistant turns, tool calls and tool observations. Treat it as more than plain chat. Calls must remain paired with their results. Intermediate failures and fixes can inform experience, but an unsuccessful trace must not be labelled a completed task.",
        ),
        (
            "本地与隔离验证", "Local and isolated verification",
            "在「轨迹验证方式」选择本地或隔离验证。隔离方式需要可用的容器服务与固定镜像，重放支持的受限工具状态，不会任意执行导入记录中的代码。环境可用不等于每个工具轨迹都已验证。",
            "Choose local or isolated verification. Isolation needs an available container service and a pinned image. It replays supported restricted-tool state, not arbitrary code from an imported record. A ready environment does not mean every tool trace has been verified.",
        ),
        (
            "缺失推理与经验", "Missing reasoning and experience",
            "导入数据库的缺失推理处理在输入节点选择。补写内容是合成解释，应与已有原生推理区分；不能让当前回合提前知道未来的工具结果。经验提案可采用或回退，已采用版本也不代表所有旧失败都已修好。",
            "Choose the policy for missing imported reasoning on the input node. Added text is a synthetic explanation, distinct from existing native reasoning. A turn must not assume a future tool result. Experience proposals can be adopted or reverted; adoption does not mean every previous failure has been repaired.",
        ),
        (
            "轨迹无法验证时", "When a trace cannot be verified",
            "先看缺的是工具结果、调用对应关系还是验证环境。环境报错可在本节点「检查隔离环境」定位；缺记录则回输入预览核对 DSH 导入。工具不在支持范围时保留未验证状态，不把文字描述当作执行回执。",
            "Determine whether the missing piece is a tool result, call pairing or the verification environment. Use Check isolated environment for setup failures. For missing records, inspect DSH import preview. Unsupported tools remain unverified; a written description is not an execution receipt.",
        ),
    ),
    "gsm8k": (
        (
            "独立的模板算术题", "Template-based arithmetic tasks",
            "这个节点按算术模板生成问题与可核算结果，不负责把上传论文里的公式自动变成算术题。需要依据论文讨论公式时，使用解析后的资料、指导员和 SFT；需要批量练习明确算术关系时，使用本节点。",
            "This node generates arithmetic tasks and checkable results from templates. It does not automatically turn formulas in an uploaded paper into arithmetic exercises. For questions about a paper's formulas, use parsed sources, the director and SFT. Use this node for batches of explicit arithmetic relationships.",
        ),
        (
            "去重与可生成空间", "Duplicates and the available problem space",
            "已生成题目的去重记录用于避免反复调用产生相同题目，但模板与参数的组合空间有限。达到范围极限后应调整生成范围或换任务，不宣称有限空间可以永远生成不重复题目。",
            "Stored duplicate records help avoid the same task across repeated generation. Template and parameter combinations are finite. At exhaustion, change the allowed range or task rather than expecting unlimited unique problems from a finite space.",
        ),
        (
            "数量不足或题目重复时", "When output is short or repetitive",
            "先查看去重与可用模板的运行记录，确认是空间已耗尽还是实际重复未被识别。调整本节点题目设置后再生成；不要关闭去重来凑数量，也不要让文档解析节点承担无关的算术核验。",
            "Inspect the run's duplicate and template records to distinguish exhaustion from a missed duplicate. Adjust this node's problem settings before generating again. Do not disable duplicate checks to meet a count or route unrelated arithmetic checks through document parsing.",
        ),
    ),
    "package": (
        (
            "交付真实可用的记录", "Deliver records with their actual status",
            "打包汇总已生成的训练文件、审核状态与产物信息。生成完成、已抽检和审核通过不是同一状态；未抽检不能计作该项评审通过。下载前先看报告里的合格、待处理和隔离分布，再选择交付内容。",
            "Packaging gathers training files, review states and artifact details. Generated, sampled and passed are different states. Unsampled records are not passes for that review. Before downloading, inspect qualified, pending and isolated counts in the report and choose what to deliver.",
        ),
        (
            "格式、推理与文件大小", "Formats, reasoning and file size",
            "按训练目标选择 CPT、SFT、多轮或偏好格式，确认目标训练器如何读取推理字段。不同格式可来自同一候选。文件大小由实际产物计算并显示合适单位；原生推理与审核记录保留在来源记录中，不因选择某个训练投影而覆盖原稿。",
            "Choose CPT, SFT, multi-turn or preference formats for the training objective. Confirm how the target trainer reads reasoning fields. Multiple formats may project the same candidate. File sizes come from actual artifacts and use suitable units. Raw reasoning and review records stay in the source records; a training projection does not overwrite them.",
        ),
        (
            "训练时适配思考配置", "Adapt thinking configuration when loading data",
            "平台保存统一思考强度，训练读取器再映射为目标模型约定。例如，只有思考开关的目标模型可由示例适配器将所有强度映射为开启；用户可自行调整策略。节点中的控制文本替换同样在读取阶段使用，不必为每种映射复制一整份数据集。",
            "The platform saves canonical thinking effort. A training loader maps it to the target model's convention. For example, an adapter for a model with only a thinking switch can map all levels to enabled; users can choose another policy. Node control-text replacements also apply when reading data, avoiding a full dataset copy for every mapping.",
        ),
        (
            "导出受阻或内容不对时", "When export is blocked or content looks wrong",
            "先按报告中的具体原因回到对应审核或来源节点。缺少合格记录时检查质量状态，字段不适配时检查训练格式，控制文字未变时检查生成节点「输出适配」与训练读取配置。不要仅修改文件名、跳过核验或把待处理项标成通过。",
            "Follow the report's specific reason back to review or source handling. For no qualified records, check quality status. For incompatible fields, check the training format. For unchanged control text, check the generation node's Output rules and training-loader settings. Renaming a file or relabelling pending records does not resolve the issue.",
        ),
    ),
}


def render_node_guide(node: str, *, language: str | None = None) -> None:
    """Render concise bilingual help inside the selected node's existing panel."""
    english = str(language or st.session_state.get("ui_language", "zh")).lower().startswith("en")
    sections = _GUIDES.get(node, (_PROMPT_EDITING,))
    for title_zh, title_en, body_zh, body_en in sections:
        title, body = (title_en, body_en) if english else (title_zh, body_zh)
        st.markdown(UntranslatedText(f"**{title}**\n\n{body}"))
