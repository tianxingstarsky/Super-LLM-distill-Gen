---
name: dataforge
description: 训练数据工厂（Super-LLM-distill-Gen）工作流：导入真实会话/文档/图片 → 蒸馏质检 → 人工审核 → 导出 SFT/DPO 训练集。使用 dataforge 工具执行；涉及付费 API 或放量前必须先过闸门（gate status）。
---

# DataForge 工作流

## 何时用哪条命令（按任务选，别绕弯子）

| 用户想要 | 命令 |
|---|---|
| 用真实会话数据做训练集 | `import`（过 G1）→ `distill --llm-check N` → `export` |
| 文档/资料变训练数据 | `doc2corpus`（CPT 知识注入）→ `doc2data --mode cross`（综合问答） |
| 身份/品牌问答训练集 | `identity-gen` |
| 中英互译语料 | `translate` |
| 图片变图文对话数据 | `vision` |
| 去 AI 味/统一文风 | `style-correct --rules 规则文件` |
| 思考风格偏好 | `cot-style` |
| 工具使用/联网任务轨迹 | `agent-gen --scenario web|code|indirect_web` |
| GUI 操作轨迹 | `gui-cot`（需截图轨迹 JSONL） |
| 偏好对增强 | `dpo-enhance --mode …` → `dpo-merge` |

## 铁律（违反会浪费用户的钱或产出脏数据）

1. 任何可能花 token 的命令（distill 打分/translate/identity-gen/doc2data/cot-style/vision/dpo-enhance/agent-gen/gui-cot/style-correct/prompt-eval）执行前，
   先 `gate status`：G0 未 approved 必须先请用户过门，不要擅自跑。
2. 导出放量（`export --bulk`）必须 G3 已 approved；未过门先用 `preview --html` 请用户过目。
3. 涉及用户私有数据（import）必须 G1 已 approved。
4. 产出文件都在 DF_ROOT/data/output 下；向用户汇报时给出具体文件路径与统计数字（`stats`/命令自带统计）。
5. 需要看效果：`preview --html --n 20` 生成人工可读页面。

## 预算与模型

- 模型选择：`models` 查网关可用模型；各角色槽位在 configs/backends.yaml 的 model_roles。
- 预算上限在 backends.yaml budget；超限会硬停（gate G0 文案中有说明）。
