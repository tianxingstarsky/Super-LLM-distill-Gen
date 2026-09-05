# 工作区与 minimind 导出

## 一、工作区：数据按区分流，不挤一锅

数据按项目/数据域/客户/批次分工作区，各自隔离：样本、语料、审核记录、DPO 对、
导出物、**闸门状态（G1 数据源 / G3 放量）**。`default` 工作区恒映射原
`data/output/`（已有数据与审核记录不动，完全向后兼容）。

| 项 | default | 其他（如 docs） |
|---|---|---|
| 输出目录 | `data/output/` | `data/workspaces/docs/output/` |
| 审核中心数据集 | `rollout_review` | `rollout_review_docs` |
| 闸门状态 G1/G3 | `data/output/gates_state.json` | `data/workspaces/docs/output/gates_state.json` |
| 协作审核收件箱 | `data/output/remote_inbox.jsonl` | `data/workspaces/docs/output/remote_inbox.jsonl` |
| **预算（G0 硬停）** | **全局**：`data/output/budget.json`，不随工作区切换 | 同左 |

```bash
df workspace list                     # 全部工作区（default 恒在）
df workspace status                   # 当前工作区：输出目录/数据集/闸门
df workspace status --ws docs         # 指定工作区
df workspace use docs                 # 持久化当前工作区（控制台/agent 默认跟随）

df import --ws docs                   # 子命令后加 --ws（所有命令通用）
df export --format minimind --ws docs
df review push --ws docs              # 推送到 rollout_review_docs
df review-remote pull --ws docs       # 协作者端同款：pull/submit 按工作区分流
```

选择优先级：`--ws` 显式 > 环境变量 `DF_WORKSPACE`（控制台子进程注入/agent 可设）>
`df workspace use` 持久化的 current.json > `default`。工作区名限
字母/数字/下划线/连字符（1-32 位，防路径穿越与数据集名污染）。

控制台（8501）侧栏顶部有**工作区选择器**：总览/资产/预览/运行/审核/监控各页的
读取路径与"管线运行"页的子进程调用都会跟随所选工作区；预算页恒显示全局预算。

## 二、minimind 数据集（可直接喂 jingyaogong/minimind 训练）

格式以 minimind `dataset/lm_dataset.py` 加载侧为准（逐字段核对）：

| 文件 | 每行结构 | 说明 |
|---|---|---|
| `sft_t2t.jsonl` | `{"conversations": [{"role","content"[,"reasoning_content"][,"tool_calls"]}]}` | 思考保留在 `reasoning_content`（与 df 的 separated 模式天然对齐）；`tool_calls` 为 JSON 字符串，加载侧 json.loads 还原 |
| `pretrain_t2t.jsonl` | `{"text": "…"}` | 由 `df doc2corpus` 的语料（`corpus/docs.jsonl`）转出，剥离 source/chunk_id |
| `dpo.jsonl` | `{"chosen": [消息列表], "rejected": [消息列表]}` | chosen/rejected 为**含 prompt 前缀的完整对话**（minimind 直接 apply_chat_template） |

```bash
df export --format minimind                    # 三件套 → data/output/export/
df export --format minimind --ws docs         # 按工作区导出（语料/DPO 源取各自工作区）
# 产物：sft_t2t.jsonl / pretrain_t2t.jsonl（有语料才写）/ dpo.jsonl（有 DPO 对才写）
```

真机验证（default 工作区，2026-09）：sft 250 行 / pretrain 17 行 / dpo 526 行，
中文正常、字段结构与 minimind 加载代码逐条对上。

## 三、与协作流程的关系

- 每个工作区有独立的审核中心数据集与协作审核收件箱；协作者拉审/提交前带
  `--ws <工作区>` 即可（数据结构上的"评审批次隔离"）。
- 放量（G3）按工作区独立：A 工作区审核达标放量不影响 B 工作区未达标状态；
  预算（花钱）不按工作区分——这是刻意的全局硬停。
```
