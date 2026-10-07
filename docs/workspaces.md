# 本机缓存与导出格式

## 开源控制台：上传资料后直接开始

开源版面向本机使用。控制台不再要求选择工作区，也不需要先登记文件夹。
用户在“自动工作流”上传文档或 Agent 上下文记录，或直接填写开放需求，然后配置节点并开始生成。
首页、数据管理、审核、任务和打包页读取同一份本机记录。

| 内容 | 默认保存位置 |
|---|---|
| 上传的来源文件 | `data/seeds/uploads/` |
| 生成任务、候选样本、审核记录与导出版本 | `data/output/` |
| 历史来源资料 | `data/seeds/`（继续兼容） |

上传后，来源文件会保存到本机缓存。刷新页面或重新打开控制台不会自动清理这些文件，
也不会自动删除已有任务和审核记录。已经保存的来源会自动加入本次资料选择，
重新打开后可直接选用本机资料，不必再次上传。

## 工作管理：历史工作与独立草稿

“工作管理”集中展示正在进行和已结束的工作。可以按任务名、来源文件名、日期或任务 ID 搜索，
再查看原始来源、运行过程、审核与导出结果。关闭浏览器或重启程序不会清空历史。
未完成的生成任务可从已保存的检查点继续，程序重启后不会自行恢复收费模型调用。

生成页自动保存当前参数与资料选择。需要同时准备多份工作时，点击“保存为独立草稿”，
随后在“工作管理”打开对应草稿。独立草稿各自保存，打开其中一份不会删除其他草稿或已生成任务。
来源文件、当前草稿、独立草稿和运行记录均保存在硬盘；关闭页面不触发清理。
节点模型和联网授权需在重新打开后确认。

| 工作记录 | 保存位置 |
|---|---|
| 当前自动保存草稿 | `data/output/.creation-draft.json` |
| 独立草稿 | `data/output/.creation-drafts/` |
| 每次生成工作及来源快照 | `data/output/workflows/<任务 ID>/` |

这些本机资料和运行记录不提交到 Git。

缓存位于运行项目的机器上。闭源服务版部署在服务器上，按登录身份和服务端隔离规则管理资料与任务；
服务版不会让使用者选择服务器上的本机文件夹。其存储和隔离方式由独立服务版实现。

## 命令行和旧路径兼容

控制台统一使用默认本机缓存。命令行保留 `df workspace` 和 `--ws`，供已有脚本和旧数据路径继续使用。
它们属于兼容接口，不是控制台的前置步骤。

```bash
df workspace add "F:\资料\我的数据"
df workspace list
df workspace use folder-3f9a2c7b1d4e
df workspace status --ws <标识>

df import --ws <标识>
df export --format minimind --ws <标识>
df review push --ws <标识>
df review-remote pull --ws <标识>
```

`workspace add` 只登记一个已存在的本机文件夹。它不复制、移动、改名或改写源文件，
也不会创建不存在的源目录。标识来自规范化路径，格式为 `<目录名slug>-<路径哈希12位>`；
目录名无法形成 ASCII slug 时使用 `folder-<路径哈希12位>`。

| 兼容来源 | 产物位置 | 审核中心数据集 |
|---|---|---|
| `default` | `data/output/` | `rollout_review` |
| 命令行登记的已有文件夹 | `<文件夹>/.dataforge/output/` | `rollout_review_<标识>` |
| 遗留 `data/workspaces/<名>/` | `data/workspaces/<名>/output/` | `rollout_review_<名>` |

旧路径不自动迁移。命令行的选择优先级仍是：显式 `--ws`、`DF_WORKSPACE`、
`df workspace use` 保存的默认值、`default`。标识限 1–48 位字母、数字、下划线和连字符。
这些命令行设置不会让开源控制台重新出现工作区选择器。

源文件清点只读且有数量上限，排除 `.dataforge/`、`.git/`、`.venv/`、`node_modules/`、
`__pycache__/`、符号链接、junction、输出目录本身和 `.gitkeep`，避免把生成产物当成来源重复读取。
`import` 和 `distill` 可继续读取指定的旧来源路径；显式 `--rollout-dir` 或 `--input` 保持优先。
`default` 保留已有配置和环境变量的回退行为。

兼容注册表 `data/workspaces.json` 保存本机路径及命令行默认值，不进入 Git。
其锁文件以及遗留的 `data/workspaces/current.json` 也保留兼容。
登记路径无效或源文件夹已经移走时，命令会报错，不会重建该源文件夹。

## 远端审核兼容

`configs/review_remote.yaml` 的 `dataset` 是实际使用的数据集 ID。
仅当配置仍是通用默认值 `rollout_review`，且命令行显式使用非 `default` 标识时，
才会映射为 `rollout_review_<标识>`。已配置的具体数据集 ID 直接生效。

本机路径的自动标识在不同机器上可能不同。连接同一个审核中心时，应显式配置中心的数据集 ID，
并与推送方保持一致。远端批次缓存按中心地址、数据集和账号隔离；重复拉取复用缓存，提交保持幂等。

## minimind 兼容导出

本项目与 minimind（jingyaogong/minimind）无关联、合作或依赖。
minimind 和 LLaMA-Factory 都只是导出格式的参照来源。

| 文件 | 每行结构 | 说明 |
|---|---|---|
| `sft_t2t.jsonl` | `{"conversations": [{"role", "content"[, "reasoning_content"][, "tool_calls"]}]}` | 思考内容单独保存在 `reasoning_content`；`tool_calls` 按目标格式保存为 JSON 字符串 |
| `pretrain_t2t.jsonl` | `{"text": "…"}` | 从 CPT 语料导出，不带内部来源和分块字段 |
| `dpo.jsonl` | `{"chosen": [消息列表], "rejected": [消息列表]}` | 两侧都是包含 prompt 前缀的完整对话 |

```bash
df export --format minimind
df export --format minimind --ws <旧标识>
```

默认导出位置为 `data/output/export/`。使用旧标识时，导出写入对应产物目录下的 `export/`。
有相应数据时才生成预训练或偏好文件。
