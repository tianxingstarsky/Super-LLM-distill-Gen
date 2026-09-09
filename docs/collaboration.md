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

## dsh 读取 Skill 后派发配置

```bash
python -m lib.cli dsh --team --ws docs --review-config quality=configs/review_remote.quality.yaml --review-config safety=configs/review_remote.safety.yaml "按 review-team 技能审核 3 条，先给候选结果，不提交"
```

该入口自动准备文件 URL 补丁、插件依赖和自定义 skill 根目录。无需拼长 shell 命令或接管用户全局技能目录。
Lead 读取 review-team skill，再把角色、工作区、配置路径和用户授权范围下发给子智能体。配置不硬编码机器路径，也不让 Lead 读取或传播密钥。
技能内含三块合同（`plugins/dsh-dataforge/skills/`）：**硬约束**（不得代批闸门/不得读密钥/未完成不得提交/不越权扩容）、
**注意要点**（缓存按中心+账号隔离、内容哈希绑定、分歧不计通过、提交幂等）、
**任务分布示范**（小批验证/双角色正式批/未完成处置/多工作区并行的可照抄任务卡与回报格式）。
quality 与 safety 使用不同审核规则。超长输入、视觉样本、网络错误或缺少证据的输出明确为“未完成”，不得伪装为 reject 或成功提交。
Agent Teams 是上游实验性功能，不等于工业级调度服务；本轮未追加付费团队模型实测。

## 汇总与导出

`review summary` 查询中心最新数据，不依赖旧缓存。响应按唯一样本聚合，存在分歧的样本不计通过。
`review pull` 只同步与统计，不再自动放行 G3。人工确认 G3 后，bulk 导出仍检查当前内容哈希、结构、重复、审核覆盖率和一致通过率。

历史 26 条演示响应保留；旧响应未记录内容哈希，不能作为修改后样本的有效放量证据。
完整门槛与已知限制见 [操作与质量保障](quality-and-operations.md)。
