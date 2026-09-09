# 操作与质量保障（当前实现边界）

## 启动与常用任务

Windows 双击 `scripts/start_all.vbs`，不打开命令行窗口。`start_all.bat` 是兼容入口。
启动器使用文件锁防止重复启动，没有看门狗、自动重启或 JVM 服务。
Streamlit UI（8501）与审核 API（6900）在同一个服务进程；后台生成任务会有独立子进程，Windows 下禁止创建窗口。

控制台侧栏选择工作区；「管线运行」选择 minimind 兼容导出、文档语料、文档问答、质量报告、AI 审核小队等预设。参数表单直接读取 CLI 定义，不再手写 JSON。
任务日志与退出码由任务对象保存，不从后台线程访问 Streamlit 会话状态。不同会话的工作区选择不修改共享环境变量。

```bash
python -m lib.cli doctor
python -m lib.cli quality-report --input data/output/rollout_samples.jsonl
python -m lib.cli export --format minimind --tag pilot-v1
python -m lib.cli dsh --team --review-config quality=configs/review_remote.quality.yaml --review-config safety=configs/review_remote.safety.yaml "按 review-team 技能审核当前工作区，先给候选判定，不提交"
```

`doctor` 只读环境和本地服务状态，不运行 pytest、不启动服务、不调用模型、不修改配置。未设置缓存目录会明确警告，而不是声称已经全部在 F 盘。

## 协作审核

- 「人工审核」页面内可验证并保存协作配置，密钥输入框隐藏内容，新配置不覆盖同名文件。
- 审核视图按**聊天气泡**渲染（user 右蓝 / assistant 左绿 / tool 紫 / system 居中灰），
  正文走 **Markdown**（粗体、列表、代码块、表格）；样本内容是**不可信数据**，原始 HTML 一律转义。
- **逐条编辑**：每条气泡下可展开编辑正文（assistant 另可编辑 reasoning_content），
  保存后生成**新版本样本**（新 sample_id `xxx-r1`，原记录保留待判）——与内容哈希绑定一致，
  旧审核票不会套用到新内容。历史无结构化 payload 的记录只能纯文本审阅（提示重新 push）。
- 深链分享：`/?page=人工审核&record=<sample_id>` 直接定位到某条待审记录（协作者交接用）。
- CLI 也支持 `review-remote setup --server URL --key-env REVIEW_KEY --config 新文件路径`。密钥从环境变量或隐藏输入读取，不放在命令行参数中。
- 工作区授权：`user grant USER --ws WORKSPACE`。新账号默认只能访问 legacy/default 数据集；其他工作区必须授权。
- 每个批次缓存按 **中心地址 + 数据集 + 审核账号** 分文件，操作加文件锁、写入原子替换。未提交的批次再次 pull 会复用，不覆盖。
- `auto` 只生成候选判定，`submit` 才提交；必须有 G0 审核预算授权。`--model` 覆盖生效。
- quality 与 safety 使用不同 rubric；完整对话作为不可信数据传入，不能把样本中的指令作为执行命令。
- 超出窗口、API 错误、格式错误、缺少理由、需要视觉审核时，标记 **未完成**，不产生 reject 投票，也不允许提交该批。
- 数据库密钥保存 SHA-256 哈希；账号列表不显示密钥。重复建同名账号会拒绝，而不是悄悄轮换别人正在使用的凭据。
- 静态文件下载和所有响应汇总要求管理员认证；记录提交校验账号权限、数据集归属、合法判定与内容哈希。

## 发布质量

`quality-report` 是确定性检查，不是事实正确性的证明。目前包含：消息结构、上下文缺失、未闭合助手回答、工具错误标记、精确重复、长度字符数和语言启发式统计。

审核证据绑定样本内容哈希。已审核记录不得在原 sample_id 下修改内容；应使用新版本 ID。历史响应没有哈希时保留原记录，但不冒充当前内容的有效通过票。
多人响应按 **唯一 sample_id** 聚合；只要存在驳回或分歧，该样本不算通过。多个机器人给同一条投票不能凑足最少样本数。

`export --bulk` 同时要求：
1. 操作人员已经确认 G3；
2. 本次输入非空、无结构问题和精确重复；
3. 与当前内容一致的审核覆盖率至少 90%；
4. 至少 10 个不同样本有效审核，样本一致通过率至少 90%。

审核汇总不自动批准 G3；旧 G3 通过也不能绕过上述实时检查。无强制跳过参数。
普通 export 是 **草稿**，仍生成质量报告。每次写独立版本目录，包含 `quality.json`、`manifest.json`、每个输出文件的 SHA-256 和源样本哈希；同名 tag 拒绝覆盖。转换异常留下 `status=writing` 的不完整版本，不冒充成功。

minimind 兼容格式导出保留 reasoning_content / tools / tool_calls，不允许静默丢弃 images。bulk 当前仅批准 SFT 输入，未审核的 corpus/DPO 不作为附带文件偷偷放量；草稿可导出已有语料与偏好对。

## 仍需明确的限制

- 本轮没有重新训练模型或进行大规模质量基准评估，不能由单元测试推断生成内容“足够优质”。
- 语言检测和字符统计是启发式，不是 tokenizer 精确计数；精确去重不是语义去重。
- 文本审核不替代视觉审核、工具重放、外部事实核验或人工验收；超长样本目前明确挂起，不自动截断认证。
- 预算累计加进程锁、请求前检查、BudgetExceeded 不重试；并行已发出的请求仍可能跨过阈值，不是预付费额度预留机制。dsh 自身的模型开销不计入 Python BudgetGuard。
- 默认仅监听回环地址。外机通过受控 SSH/TLS 隧道接入，不应直接向公网暴露控制台。
- 服务更新前使用 SQLite backup API 做一致性备份，尤其 WAL 模式不能只随意拷贝主数据库文件。
