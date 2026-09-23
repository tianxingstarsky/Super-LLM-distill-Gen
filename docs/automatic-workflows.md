# 自动训练数据工作流

控制台「自动工作流」会为每次运行保存独立的来源快照、配置、阶段状态、检查点、质量报告和训练产物。使用者可以上传文档或 Agent 对话记录，也可以直接填写开放性需求；选择训练目标、任务量和文档分块大小后即可启动。页面用连线展示每个处理阶段；选择节点可查看该阶段使用的来源/模型/参数、进度、错误和运行事件。任务支持停止、断点续跑和下载带 SHA-256 清单的 ZIP。

```text
文档 / Agent 上下文 / 开放需求
  → 解析、隐私筛查、结构检查与来源定位
  → 按所选目标执行生成、派生或本地校验
  → JEV 五维评分、算术校验、去重和失败隔离
  → 训练文件 + 逐条质量记录 + 可校验清单
```

## 输入与处理方式

文档支持 TXT、Markdown、PDF、DOCX，按字符数切块并保留标题、数字和段落顺序。PDF 与 DOCX 解析依赖 `pypdf` 和 `python-docx`。JSON / JSONL 支持 `text` 语料、ShareGPT/OpenAI `messages` 或 `conversations`，也支持包含完整闭合上下文的 ZCode model-io 记录。缺少上下文、过长或疑似包含密钥/个人数据的输入会隔离并记录原因。带工具错误的轨迹仅在选择 Agent 目标且结构完整时保留，用于验证后的独立失败轨迹；其他目标继续隔离。图像和音频输入需专用多模态工作流。

开放需求先规划为多个独立任务，再按目标执行。任务文本和来源内容均作为不可信数据处理；提示词要求模型遵循 schema，不执行资料中嵌入的指令。文档来源会保留来源文件哈希和定位；开放需求合成内容会标记为 synthetic。字符分块并不等同 tokenizer 长度，超长输入会在模型调用前隔离。

## 训练目标与产物

| 目标 | 当前工作流行为 | 训练文件 schema |
| --- | --- | --- |
| CPT | 文档片段保留原文与来源定位，记录质量信号；本批次及同一工作区已发布的 CPT 版本做保守精确/近重复筛查；可选上传本地评测参照进行重叠隔离；开放需求生成合成知识语料并经 JEV 评审 | `{"text":"..."}` |
| SFT | 根据文档、闭合对话记录或开放任务生成可训练问答；文档问答必须带可逐字匹配的来源证据 | `{"messages":[...]}` |
| 多轮对话 | 开放需求或文档逐轮生成至少两轮用户与助手交互，每轮 JEV 评审后再检查全段一致性；导入完整对话仅评审、不改写原始消息 | `{"messages":[...],"tools":[...]}`（有定义时保留） |
| Agent | 仅从已记录的完整工具轨迹派生；默认有限重放支持受限整数 `calculator` 与录制来源快照上的 `json_pointer`；管理员显式配置固定摘要的官方 Python 容器后，可重放受限 `sandbox_ledger` 状态任务，其他工具进入隔离 | `{"messages":[...],"tools":[...]}`（有定义时保留） |
| DPO | 对通过 SFT 的同一上下文生成完整候选，用 JEV 比较；偏好差至少 2 分才输出 | `{"prompt":[...],"chosen":[...],"rejected":[...]}` |
| RLAIF | 将同一偏好对转换为带排序、维度分数和评语的 AI 反馈记录，并另导出奖励模型可用的偏好三元组 | `{"prompt":[...],"responses":[...],"criterion":"correctness"}`；`trl_rlaif.jsonl` 为 `prompt/chosen/rejected` |
| ORPO | 从通过比较的偏好候选输出 ORPO 常用的 prompt/chosen/rejected 格式 | `{"prompt":[...],"chosen":[...],"rejected":[...]}` |
| GSM8K | 本地模板生成整数算术文字题，用受限 AST 计算器核对每一步及最终结果 | `{"question":"...","answer":"... #### n"}` |
| CoT | 从已通过 SFT 的回答派生简洁、可见的推理解释，并由 JEV 再次核对 | `{"question":[...],"reasoning":[...],"answer":"..."}` |

GSM8K 格式当前由安全、确定性的基础整数算术模板生成，使用加减乘整除，不调用模型；它适合离线验证和小规模样例生成，不等同于完整 GSM8K 题库、自然语言数学推理覆盖或 benchmark。CoT 的 reasoning 字段保存可检查的可见解释，不声称恢复模型的隐藏思维。若只选择 CPT，文档原文处理无需模型；开放需求的 CPT 会调用生成模型和 JEV。DPO、RLAIF、ORPO 共用一次偏好比较，避免为每种格式重复生成候选。

多轮目标使用独立的 `multiturn` 训练文件。开放需求与文档可设置 2–8 轮（默认 3 轮）；系统逐轮生成后分别检查回答，再检查整段是否自洽。文档回答需要逐字原文引文，首轮训练提示包含对应资料；开放需求不伪造外部引文。已导入的完整对话保持消息原样，逐轮评审和整段评审记录在 `multiturn.records.json`。来源记录区分合成对话、录制上下文与文档依据，并明确标记事实未经独立核实；导入对话中的工具调用目前尚未重放验证。

Agent 正例要求录制的完整工具调用与观测，并在可重放的受限工具上逐轮核对调用闭合、结果和严格格式的最终回答；最后一轮正确不能掩盖前面错误的回答。默认本地重放执行无文件系统、网络或任意代码权限的确定性适配器，不能证明上传内容的真实性。`calculator` 只接受长度不超过 256 字符的受限整数表达式；`json_pointer` 只接受 `snapshot_id` 和 RFC 6901 `pointer`，读取同一 JSON/JSONL 来源记录顶层的 `tool_snapshots`，不读取调用参数内的临时文档、工作区文件或在线服务。该来源文件在创建运行时复制并由 SHA-256 固定；每次 JSON 查询的快照摘要保存在质量记录中。最多 16 个快照、合计 32768 字符、2048 个节点、深度 16，查询结果只支持 JSON 标量。同轮不同工具结果无法确认最终答案对应关系，会进入隔离。

可选的容器重放需要在**创建任务前**由部署者设置 `DATAFORGE_AGENT_REPLAY_IMAGE=docker.io/library/python@sha256:<64 位十六进制摘要>`，并预先把这个精确镜像拉取到 Linux Docker daemon。仅接受官方 Python 仓库的摘要形式；镜像身份会写入不可变的运行配方，恢复任务不读取新的环境值。运行时使用 `--pull=never`，缺少镜像、Docker CLI 或 daemon 时，相关记录以 `container_replay_unavailable` 隔离，**不会生成正例或负例**；未用容器工具的 `calculator`/`json_pointer` 轨迹继续正常重放。当前开发机 Docker CLI 已安装而 daemon 不可用，因此容器成功执行尚未在本机验证。

第一种容器工具是 `sandbox_ledger`。来源记录在 `messages` 外提供 `tool_snapshots`，其中一个快照必须严格包含 `kind:"bounded_ledger_v1"`、`balances` 和 `goal_balances`：2–16 个同名账户、非负整数余额、总额不变，目标必须不同于初始状态。工具调用只能是 `balance(snapshot_id, account)` 或 `transfer(snapshot_id, from, to, amount)` 的 JSON 参数；`transfer` 只能把正整数余额在现有账户间转移，不能透支。每次调用在全新容器中以固定程序处理 stdin 中的有界 JSON 状态，应用端还会独立核对返回值、转账后余额和目标判定。容器使用无网络、只读根目录、非 root 身份、丢弃全部 capabilities、内建 seccomp、禁止新权限；不映射宿主工作区、Docker socket、设备、端口或环境密钥；并限制 PID、内存、CPU、文件描述符与单步时间。终态必须与快照声明的 `goal_balances` **完全相同**；观测与实际执行不符或终态未达成时，才有足够的重放依据进入独立失败轨迹。执行记录保留镜像及固定程序摘要、来源快照摘要、每步状态摘要与终态判定。这个谓词仅证明**在上传的任务状态内**完成了目标，不证明外部账户或事实的真实性。

例如 `"tool_snapshots":{"allocation":{"kind":"bounded_ledger_v1","balances":{"alpha":3,"beta":0},"goal_balances":{"alpha":1,"beta":2}}}`，工具调用可为 `sandbox_ledger({"snapshot_id":"allocation","action":"transfer","from":"alpha","to":"beta","amount":2})`，实际容器结果应为整数 `2`，最终回答也须严格为 `2`。任意 shell 命令、Python 代码、文件读写、网络查询或模型自报的工具结果都不属于该工具合同。

仅有开放需求时，当前系统仍以 `recorded_tool_trajectory_required` 拒绝 Agent 正例。要形成自动生成的**可验证**轨迹，下一步需由可信任务构建器把需求编译成有版本的初始状态和独立终态谓词，由模型只提出受控工具动作，再由容器逐步执行并把真实结果回填为观测，最后用同一初始状态核验成功、失败与修复分支。模型自行编写的快照、观测或成功声明不能直接升格为执行证据。

例如一个记录可在 `messages` 外提供 `"tool_snapshots":{"inventory":{"qty":3}}`，其中工具调用为 `json_pointer({"snapshot_id":"inventory","pointer":"/qty"})`，观测为 `{"result":3}`。这只证明给定录制快照上的读取结果为 3，**不证明库存现实数量为 3**。数值观测可用单独整数或仅含 `result` 的 JSON 对象；其他标量观测需用 JSON 字面值或 `result` 对象。数值答案接受单独数字或“答案是 3”等受限句式；字符串答案需与结果完全相同，其他表述隔离以免误判。

只有适配器成功重放且录制观测给出可比较的不同值、录制 `isError: true` 却重放成功、严格格式的答案与唯一结果不符，或容器状态的终态谓词未达成，才写入单独的 `agent.negative.jsonl`。负面记录保留截至失败步骤的原始消息、来源和执行证据，不混入正例训练文件。未知工具、缺失或超限快照、不支持的表达式、容器不可用、无法解析的观测、同轮不同结果及无法确定性核验的答案只进入隔离记录。轨迹剪枝仅移除相邻、静默、调用及观测完全相同且后文不引用其调用 ID 的重复对；原始轨迹哈希、已验证调用和剪枝 ID 留在质量记录。重放规则与容器程序摘要进入 Agent 检查点键，规则变化会使未完成运行的 Agent 阶段检查点重新计算。新工具必须有专用的有界执行合同、结果比较规则与独立终态谓词；历史 `agent_gen` 的 `synthetic_unverified` 模拟观测不能当作执行证明。

CPT 分块保留来源文件、位置、质量信号和保留/隔离原因；过长的单个段落会继续保序切分。打包对本次选中的语料做规范化精确去重，并对长度至少 300 字符的普通长文本用字符 7-gram、Jaccard ≥ 0.90 做保守近重复标注。代码、表格和公式只做精确去重。逐条记录保存重复源、相似度和簇大小，质量报告展示解析、质量、去重和最终保留率，并按每份来源列出最终导出、重复和隔离数量，便于定位低保留资料；解析阶段尚未进入 CPT 的隔离输入另列。来源质量汇总只含计数和原因，不复制被隔离的评测命中文本。这些阈值是本项目的工程选择，需在真实领域语料上校准；近重复索引仍可能漏检。

新建 CPT 任务时，系统自动检索**当前工作区**之前生成的 `releases/cpt-vNNNN/` 发布版本，仅把标记为人工审核发布、文件清单及 SHA-256 均可校验的版本锁定为跨批参照。未审核的自动候选不会影响后续任务。运行配方保存每个参照版本的发布清单与语料哈希，打包时再次校验；版本在任务创建后被改动会使任务失败，而新发布的版本不会悄悄改变已创建任务的结果。跨批先按 NFC 与折叠空白后的文本 SHA-256 做精确重叠筛查，再对普通长文本使用与批内一致的字符 7-gram 候选索引和 Jaccard ≥ 0.90 核验；数字序列不同以及代码、表格、公式不按近重复过滤。匹配项不进入新的训练文件，记录对应运行、版本、行号、相似度及 `released_corpus_exact_duplicate` 或 `released_corpus_near_duplicate` 原因。质量报告分别展示批内与跨批的精确、近重复数量。此机制不覆盖其他工作区或未发布候选；候选索引仍可能漏检，发布语料越多，索引的内存和处理成本越高。清单校验也不等于审核身份、版权许可或事实正确性。旧版配方没有锁定参照版本，仍按原先批内规则运行。

需要检查具体评测集污染时，可在生成页的「预训练评测集去污染」上传自备 UTF-8 JSON 或 JSONL，每条记录仅含 `{"text":"评测题目或参照文本"}`。单文件最多 5 MiB、最多 20 份、合计最多 25 MiB、总计最多 10,000 条。评测内容只在本地快照，**不作为训练来源，也不发送给生成模型或打进训练产物**。任务创建时固定文件哈希，打包时再次核验；快照若被改动，任务失败而非跳过去污染。规范化全文相同或至少 24 字符的参照原文嵌入 CPT 片段时隔离；长度足够的普通长文本还会做保守近重复检查。质量报告记录命中数量与参照 ID、文件名、记录序号，不复制评测正文。短参照只能查全文相同，未上传时报告明确标记「未配置」；两种情况都不能证明与其他外部评测集无重叠。

对 SFT、多轮对话、Agent、DPO、ORPO、RLAIF，打包阶段另做 TRL 对话或显式偏好格式适配。只有某目标的**全部合格样本**都通过角色、工具 JSON schema、调用结果闭合及多模态边界检查，才写出 `trl_<目标>.jsonl`。如果有一条不兼容，原生训练文件仍保留，`quality.json` 的 `trainer_exports` 会列出逐条失败原因，不生成部分 TRL 文件。格式相容仍需用具体模型的聊天模板、分词器及训练脚本验证。

当前 ORPO 输出的是适用于显式偏好格式的候选对；训练阶段仍需按具体模型的 chat template、截断策略和 loss mask 做兼容验证。RLAIF 的 `rlaif.jsonl` 是 JEV 对两个候选分别评审所得的带证据标签；若顶层正确性分数与五维评分中的正确性不一致、理由与评审记录不一致，或偏好分差不足，该目标的样本会隔离。`trl_rlaif.jsonl` 只保留同一上下文的 `prompt/chosen/rejected`，可作为 TRL RewardTrainer 的偏好数据输入，评语和原始分数保存在 `rlaif.records.json`。这一步既未训练奖励模型，也未运行策略优化；当前准则是任务正确性，不是用户提供的宪法原则，且分别打分不等于独立的成对偏好复核。生成页与报告仍将产物标为候选数据。

## JEV 专用评分角色

生成模型负责生成，JEV 负责独立五维评分：正确性、推理有效性、依据一致性、指令符合度和安全性。各项为 1–5 分，输出还需要真实布尔类型的 `keep`、`grounded`、`reasoning_valid` 和具体理由；缺字段、类型错误、低分和评分结论不一致均拒绝或停止当前单元。`grounded` 表示符合给定来源或用户任务，不代表已由外部事实源独立证实。

JEV 有独立角色槽位，可在控制台「模型与密钥」配置，也可在新工作流表单中按运行覆盖。配置优先级为运行覆盖、`JEV_BACKEND` / `JEV_MODEL` 环境变量、`model_roles.jev` 专用配置。每次运行记录生成模型与 JEV 的模型身份、端点指纹和分角色 token 用量。JEV 可配置为与生成模型相同的服务，但若希望模型评审真正独立，应选择不同模型或不同服务；即便如此，自动评分仍不能替代人工审核或事实证明。

## 质量、恢复与发布边界

SFT 文档问题在实际训练 prompt 中包含对应来源片段，生成答案需提供可逐字匹配的引文。Agent SFT 保留完整对话与工具定义，不合成没有执行依据的工具调用。DPO 只比较共享上下文下的完整答案；证据不足、答案相同或分差不足的对进入隔离，不会通过随机截断答案伪造 rejected 样本。训练字段哈希用于目标内去重。

每条记录旁保存来源 ID、引用、评审依据、偏好维度、失败原因和证据等级；打包文件由 SHA-256 manifest 校验。运行使用来源快照、版本化 recipe、提示词指纹和逐单元检查点；来源、配置或提示词变更后需新建运行。中断后可复用成功响应，修改后的无效评审响应不会被当作成功检查点。达到输入处理上限时，未处理数量会显示在质量报告中。

SFT 自动候选可在控制台「人工审核 / 模型对齐」中逐条复核。审核者可修改助手回答与推理说明；prompt、角色、工具调用和其他消息元数据保持锁定。每次操作记录样本指纹、审核身份、时间和意见。只有队列全部通过或退回且至少一条通过时，才可生成仅含通过对话的独立版本，写入 `releases/sft-vNNNN/`。

DPO 与 ORPO 自动候选在同一工作区按目标分别逐对复核。审核者可以通过、退回、跳过、交换 chosen/rejected 方向或修订回答；每次操作记录目标、样本指纹、审核身份、时间和意见。跳过样本仍视为待处理。只有该目标队列全部通过或退回且至少一对通过时，才可生成独立的人工审核版本。DPO 使用 `human-review/preferences.json` 与 `releases/preference-vNNNN/dpo.jsonl`，ORPO 使用 `human-review/preferences-orpo.json` 与 `releases/orpo-preference-vNNNN/orpo.jsonl`。两个目标的审核事件、版本号和下载包相互隔离；版本包含相应目标的 JSONL、审核事件和标明目标的 SHA-256 清单，不会覆盖自动候选 ZIP。RLAIF 目前没有人工审核发布入口，仍属于自动候选。

CPT 自动候选可在同一审核工作区逐条确认。页面可查看来源文件位置、语料类型、证据等级和 JEV 记录；审核者可以修改语料、通过、退回或暂时跳过。所有候选必须通过或退回后才能打包，发布 ZIP 只包含通过项，并附带审核事件、来源产物指纹和 SHA-256 清单，保存到 `releases/cpt-vNNNN/`，原候选保持不变。

自动输出属于待审核候选。系统不会替用户声明版权授权、隐私审查、人工审核或发布闸门通过。所有模型评审、模板合成和规则核验的能力边界会写入质量报告；数据质量分数不是可发布证明。

控制台「输出打包」同时列出已完成工作流的候选包和当前工作区已有的审核发布、历史导出版本。历史版本只有清单状态完整且列出的文件通过 SHA-256 核验时才提供直接下载；旧版未列入哈希清单的 `quality.json` 只显示本地位置，不能标为已校验下载。首页的发布版本数只统计这些通过校验的版本，未完成或损坏目录仍可在打包页定位原因。超过 50 MiB 的单文件显示本地路径，避免浏览器一次读入整个训练文件。

## 启动示例

```powershell
python -m lib.cli workflow --input guide.pdf --targets cpt,sft,dpo --name "产品手册训练数据"
python -m lib.cli workflow --input guide.pdf --targets cpt --evaluation-reference heldout.jsonl
python -m lib.cli workflow --brief "为设备维护助手编写训练任务" --targets sft,multiturn,dpo,gsm8k --tasks 20 --conversation-turns 3 --jev-backend deepseek --jev-model deepseek-v4-pro
python -m lib.cli workflow --action list
python -m lib.cli workflow --action resume --run-id <32位运行 ID>
```

CLI 默认目标为 `cpt,sft,dpo`。Streamlit 新建工作流页的「自动推荐」预设勾选 CPT、SFT、ORPO、DPO；也可切换预训练、多轮对话、Agent 轨迹、偏好对齐或数学推理预设，并逐项增删目标。`--max-units` 默认 100，`--chunk-chars` 默认 2000，开放需求 `--tasks` 默认 10；这些上限可以控制单次处理量和模型费用。云端模型使用 `configs/backends.yaml` 配置的密钥与总预算，只有用户启动工作流后，来源内容才会发送到配置的模型服务。

## 参考依据与后续验收

- [TRL 数据格式与工具调用文档](https://huggingface.co/docs/trl/main/dataset_formats)区分普通对话、显式偏好对和工具调用记录。当前导出适配检查工具 JSON schema、调用与结果对应关系；具体模型的 chat template 仍需验证。
- [ORPO 论文](https://arxiv.org/abs/2403.07691)说明偏好训练目标；本平台还需要实测 ORPO 训练器的输入兼容与截断后差异，才能宣称训练可直接使用。
- [Constitutional AI](https://arxiv.org/abs/2212.08073)区分基于原则的 AI 偏好标签、偏好模型和后续 RL；[RLAIF](https://arxiv.org/abs/2309.00267)说明了以 AI 偏好训练奖励模型及直接由 AI 提供 RL 奖励的不同路径。[TRL RewardTrainer](https://huggingface.co/docs/trl/main/reward_trainer)支持显式 `prompt/chosen/rejected` 偏好数据。当前新增的是可交给奖励模型训练器的**候选数据格式**，没有训练奖励模型、在线奖励或策略优化。
- [OpenAI 函数调用文档](https://developers.openai.com/api/docs/guides/function-calling)区分模型提出的调用、应用执行和按调用 ID 返回的结果；本工作流只为已录制且本地可重放的调用提供执行证据。[MCP 官方工具文档](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)将 `isError` 作为工具级错误结果标志，它本身仍只是来源记录的声明。[RFC 6901](https://www.rfc-editor.org/rfc/rfc6901)定义 JSON Pointer 的转义和数组索引语义。
- [Docker 官方 `docker run` 参考](https://docs.docker.com/reference/cli/docker/container/run/)定义只读根、`--pull=never`、网络隔离和资源/权限限制；[SWE-bench 官方 Docker 指南](https://www.swebench.com/SWE-bench/guides/docker_setup/)以容器固定执行环境。当前 Agent 容器仅支持上述受限账本状态机，尚无 SWE-bench 式代码仓库、浏览器或通用外部工具执行环境。
- [UltraChat](https://github.com/thunlp/ultrachat)与 [MT-Bench](https://arxiv.org/abs/2306.05685)说明多轮生成和整段一致性评估的重要性。当前已能从开放需求生成完整多轮对话，并分别检查逐轮回答和全段一致性；这仍是模型评审，不能取代事实核验或工具重放。
- [FineWeb](https://arxiv.org/abs/2406.17557)、[DCLM](https://arxiv.org/abs/2406.11794)和 [Dolma 工具链](https://allenai.github.io/dolma/)提供高质量预训练语料清洗、过滤与去重参考。当前 CPT 已做批内去重、质量信号、同工作区已发布版本的跨批查重，以及自备评测集的可选重叠隔离；仍需来源许可核验、更全面的基准覆盖、规模化索引、tokenizer 打包和训练后数据效用验收。
