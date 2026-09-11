# 工作区与导出格式（文件夹优先；minimind 兼容）

> 说明：本项目与 minimind（jingyaogong/minimind）**无任何关联、合作或依赖**。
> minimind 只是一个**输出格式参照**（和 LLaMA-Factory 一样属于"格式规范来源"）：
> 我们按用户要求产出与之加载侧字段一致的文件，方便直接喂对应训练脚本。

## 一、工作区 = 你已有的数据文件夹（不搬家、不改源文件）

工作区是**你本机已准备好的目录**，不是仓库里的副本：`df workspace add <已有路径>` 只登记路径，
返回稳定自动标识 `<目录名slug>-<路径哈希12位>`（同一台机器、同一路径恒定；目录名为中文等
非 ASCII 时取 `folder-<哈希12位>`）。**不复制、不移动、不改名、不写入任何源文件**；
运行产物只写进该目录下的隐藏目录 `.dataforge/output/`。

```bash
df workspace add "F:\资料\我的数据"          # 打开已有目录 → 返回稳定标识（不创建目录，源文件原样）
df workspace list                            # 全部工作区（default 恒在；旧工作区有标注）
df workspace use folder-3f9a2c7b1d4e        # 持久化默认工作区（写入本机注册表 current）
df workspace status --ws <标识>              # 指定工作区：输出目录/审核数据集/闸门状态

df import --ws <标识>                        # 子命令后加 --ws（所有命令通用）
df export --format minimind --ws <标识>
df review push --ws <标识>                   # 推送到 rollout_review_<标识>
df review-remote pull --ws <标识>            # 协作者端同款：pull/submit 按工作区分流
```

| 情况 | 输出目录 | 审核中心数据集 | 说明 |
|---|---|---|---|
| `default`（历史） | `data/output/` | `rollout_review` | 已有数据与审核记录不动，完全向后兼容 |
| `add` 打开的新文件夹 | `<文件夹>/.dataforge/output/` | `rollout_review_<标识>` | 产物写在源文件夹内隐藏目录；源文件保持原样 |
| 旧工作区（遗留 `data/workspaces/<名>/`） | `data/workspaces/<名>/output/` | `rollout_review_<名>` | 保留兼容：不迁移、不搬运、不重建 |
| 闸门状态 G1/G3 | 各自输出目录下 `gates_state.json` | — | 按工作区独立 |
| 协作审核收件箱 | 各自输出目录下 `review_batches/<哈希>.json` | — | 按 中心机+数据集+账号 隔离 |
| **预算（G0 硬停）** | **全局**：`data/output/budget.json` | — | 不随工作区切换（刻意全局硬停） |

选择优先级：`--ws` 显式 > 环境变量 `DF_WORKSPACE`（控制台子进程注入/agent 可设）>
`df workspace use` 持久化的 current > `default`。标识限字母/数字/下划线/连字符（1-48 位）。
`add` 要求目录已存在（绝不创建）；`use` 会先校验源目录仍可用（已移走的目录不会被重建）。

**打开后的读写边界**（`lib/workspace.py`）：
- 源文件清点是**只读且有上限**（500 个）的清点：排除 `.dataforge/`、`.git/`、`.venv/`、
  `node_modules/`、`__pycache__/`、符号链接/junction、输出目录本身与 `.gitkeep`——
  不会自我摄取，也不会改动源文件。
- `import`/`distill` 缺省读所选文件夹；`translate` 缺省读 `<文件夹>/topics.txt`；
  `--rollout-dir`/`--input` 显式覆盖；`default` 保持历史回退（环境变量/配置解析）。
- 放量（G3）按工作区独立：A 区审核达标放量不影响 B 区未达标状态；预算（花钱）不按工作区分。

## 二、本机注册表（机器私有，不入库）

- `data/workspaces.json`：记录"标识 → 本机绝对路径"与 current，**机器私有**（含本机路径），
  已 gitignore，连同并发锁 `data/workspaces.json.lock`。请勿手改；格式不对会明确报错且原文件不动。
- 旧 `data/workspaces/` 目录与 `data/workspaces/current.json` 仅作**遗留回退**保留，不迁移不搬运。

## 三、控制台（8501）：选择是会话级的

- 侧栏"打开文件夹…" = 调 `add`（只登记，不改全局默认）；下拉选择器只影响**当前会话**
  （session_state + `?ws=` 链接参数），**不会**改写 `df workspace use` 的持久默认。
- 总览/资产/预览/运行/审核/监控各页的读取路径与"管线运行"页的子进程调用都带显式 `--ws`
  跟随所选文件夹；`?ws=` 指向本机未打开的文件夹时提示并回落 default。预算页恒显示全局预算。

## 四、远端协作：中心数据集标识要显式配置

- `configs/review_remote.yaml` 里的 `dataset` 是**权威值**：只有当配置仍是通用默认
  `rollout_review`、且本机在用非 default 工作区时，CLI 才把它映射为 `rollout_review_<本地标识>`；
  配置里写了具体数据集就按配置执行（`df review-remote setup` 写入 setup 时本机标识对应的数据集）。
- **不要假设"同一绝对文件夹路径在不同主机得到同一标识"**：自动标识来自本机规范化路径的哈希，
  换主机/换挂载点就会变。要审中心机的指定数据集，就在配置里显式写中心机数据集 ID，
  并确保与推送方（`df review push --ws <标识>`）的数据集一致。
- pull/auto/human/submit 支持 `--ws`；本地批次缓存按 中心机地址+数据集+账号 哈希隔离，
  `submit` 幂等、重复 pull 复用缓存不覆盖。

## 五、minimind 兼容格式（格式参照，非关联项目）

字段以 minimind `dataset/lm_dataset.py` 加载侧为准（逐字段核对，仅为格式对齐）：

| 文件 | 每行结构 | 说明 |
|---|---|---|
| `sft_t2t.jsonl` | `{"conversations": [{"role","content"[,"reasoning_content"][,"tool_calls"]}]}` | 思考保留在 `reasoning_content`（与 df 的 separated 模式天然对齐）；`tool_calls` 为 JSON 字符串，加载侧 json.loads 还原 |
| `pretrain_t2t.jsonl` | `{"text": "…"}` | 由 `df doc2corpus` 的语料（`corpus/docs.jsonl`）转出，剥离 source/chunk_id |
| `dpo.jsonl` | `{"chosen": [消息列表], "rejected": [消息列表]}` | chosen/rejected 为**含 prompt 前缀的完整对话**（minimind 直接 apply_chat_template） |

```bash
df export --format minimind                    # 兼容格式三文件 → 当前工作区 output/export/
df export --format minimind --ws <标识>        # 按工作区导出（语料/DPO 源取各自工作区）
# 产物：sft_t2t.jsonl / pretrain_t2t.jsonl（有语料才写）/ dpo.jsonl（有 DPO 对才写）
```

真机验证（default 工作区，2026-09）：sft 250 行 / pretrain 17 行 / dpo 526 行，
中文正常、字段结构与其加载代码逐条对上（格式参照）。
