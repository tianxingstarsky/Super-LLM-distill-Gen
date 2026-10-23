> dsh 使用独立安装、独立版本的[适配包](../plugins/dsh-dataforge/README.md)。产品不再启动、构建或修改 dsh。

# 协作审核与 dsh 子智能体

## 中心机

从项目根启动 `python -m lib.cli console`，或 Windows 双击 `scripts/start_all.vbs`。
UI 与协作 API 共用一个服务进程，默认仅监听本机 8501/6900，没有看门狗。

```bash
python -m lib.cli user create reviewer_a
python -m lib.cli user grant reviewer_a --ws docs
python -m lib.cli review push --ws docs
```

创建时密钥只显示一次。重复创建同名账号不轮换密钥。`user list` 不列密钥。
管理员具有全部数据集访问权；普通审核员默认允许 legacy/default 数据集，其他工作区必须显式授权。
外机连接使用受控 SSH/TLS 隧道，不要直接公开控制台。

## 协作者

在自己的主机设置自己的模型后端，审核中心只接收判定结果。
控制台「人工审核 → 协作者接入配置」可验证并保存配置；CLI 同样支持：

```bash
python -m lib.cli review-remote setup --server http://127.0.0.1:6900 --key-env REVIEW_KEY --config configs/review_remote.reviewer_a.yaml --ws docs
python -m lib.cli review-remote pull --config configs/review_remote.reviewer_a.yaml --ws docs --batch 3
python -m lib.cli review-remote auto --config configs/review_remote.reviewer_a.yaml --ws docs --policy quality
python -m lib.cli review-remote submit --config configs/review_remote.reviewer_a.yaml --ws docs
```

`REVIEW_KEY` 是本机环境变量，不要把密钥放进命令行或版本库。
`auto` 需要该工作区的 G0 审核预算授权；判定只落本地，确认后才 submit。
缓存按中心地址、数据集、账号分别存放于工作区 `review_batches/`，原子写入并加文件锁。
未提交批次重复 pull 会复用原批次，不覆盖；提交可安全重试，不重复计票。

## dsh 独立调度

在已有 Harness 中安装适配包，并显式配置允许管理的核心安装路径。先调用 `dataforge` 的 `capabilities`；需要执行时，由操作人员启用适配器的写入权限，再使用 `dataforge_dispatch` 提交已冻结的 run id。

适配器使用官方 Jobs 和受管子进程，提供优先级、依赖关系、有界等待队列和任务进度。核心保留工作流检查点、原料缓存、训练产物及模型并发限制。失败或取消的前置任务会阻止后续任务执行；依赖不会隐式改写下游配方或传递资料。

`skills/` 是可选的外部调度说明，需自行安装到 Harness 的技能目录；产品不接管全局技能。生成和评审提示词仍在工作流节点设置。当前控制协议不提供协作审核中心的账号管理或远程提交动作，上述 `review-remote` 操作继续通过核心的明确入口使用。

包版本、SDK 兼容范围和测试独立维护。Harness 重启会丢失其进程内任务句柄，但核心 run id 可用于查询和恢复。本轮验证了真实 Harness 的只读工具调用，没有运行付费团队模型任务。

## 汇总与导出

`review summary` 查询中心最新数据，不依赖旧缓存。响应按唯一样本聚合，存在分歧的样本不计通过。
`review pull` 只同步与统计，不再自动放行 G3。人工确认 G3 后，bulk 导出仍检查当前内容哈希、结构、重复、审核覆盖率和一致通过率。

历史 26 条演示响应保留；旧响应未记录内容哈希，不能作为修改后样本的有效放量证据。
完整门槛与已知限制见 [操作与质量保障](quality-and-operations.md)。
