# 参数调优指南（configs/pipelines/*.yaml 全参数速查）

每个参数：**含义 → 调大效果 → 调小效果 → 建议区间**。
交互式调参：把参数 yaml + 症状交给 `tuning.advisor` 提示词（`lib/prompts/tuning.py`），
得到逐参数解释与具体调整建议（提示词内已含本速查知识）。

## LLM 调用层（所有管线共用）

| 参数 | 含义 | 调大效果 | 调小效果 | 建议 |
|---|---|---|---|---|
| temperature | 采样随机性 | 输出多样、新颖，但格式/事实稳定性下降 | 稳定、一致，但易重复、模板化 | 生成类 0.8–1.0；判定/JSON 类 0.2–0.3 |
| thinking | 思考模式开关 | 推理质量高；但耗 token，严格 JSON 任务答案易进 reasoning 导致 content 空 | 直接输出，省 token，JSON 稳定 | **JSON 任务必须 false**；开放式创作可 true |
| max_tokens | 输出长度上限 | 更完整，但成本上升 | 省成本，但可能截断 | null=不限（思考可无限长） |
| retries | 失败重试次数 | 更稳，但更贵更慢 | 省成本，偶发空回复时失败率上升 | 3 |
| json_mode | response_format 强制 JSON | 解码层保证合法 JSON | 靠提示词+容错解析（API 不支持时自动降级） | 支持时 true |
| backend / model | 模型选择 | — | — | judge 用 v4-pro；生成用 v4-flash 控成本 |

## 管线层

| 参数（配置文件） | 含义 | 调大效果 | 调小效果 | 建议 |
|---|---|---|---|---|
| translation.faithful_threshold | 回译忠实度保留线 | 语料质量高、保留少 | 保留多、质量下降 | 4 |
| doc2data.qa_per_chunk | 每块问答数 | 样本多，同质性上升 | 样本精，覆盖少 | 3 |
| doc2data.ground_check | 事实依据校验开关 | — | — | true（防幻觉底线） |
| doc2corpus.chunk_size | 分块目标字符数 | 上下文完整、单块成本高 | 聚焦、但知识割裂 | 1500–2500 |
| doc2corpus.overlap | 块间回退字符数 | 上下文连续、语料冗余 | 省 token、边界信息丢失 | 0（CPT 语料可 100–200） |
| distill.llm_check_n | judge 抽检条数 | 质检更全、成本↑ | 成本↓、质检盲区↑ | 5（小样本） |
| review.pass_threshold | 审核放行通过率 | 更严 | 更松 | 0.9 |
| review.min_reviewed | 最少审核条数 | 放行更可信 | 更快放行 | 10 |
| rollout.truncate_chars | 超长轮次截断阈值 | 保留更多上下文、成本↑ | 防爆上下文更狠 | 8000 |

## 预算

- `backends.yaml → budget.max_total_usd`：累计成本硬停线（BudgetGuard 持久化到
  data/output/budget.json；清零即重置）。
- `backends.<名称>.prices`：每百万 token 美元价（估算值，按官网修正后预算才准确）。

## 常见症状速查

| 症状 | 优先动什么 |
|---|---|
| 生成重复/模板化 | temperature ↑；对应管线 qa_per_chunk ↓；检查开篇多样性指标 |
| JSON 解析失败多 | thinking=false；json_mode=true；确认 API 支持 response_format |
| 空回复多 | retries ↑；max_tokens=None；judge 换 v4-pro |
| 语料质量低 | faithful_threshold/ground_check 阈值 ↑ |
| 语料太少 | 阈值 ↓ 或 qa_per_chunk ↑（注意同质性上升） |
| 成本超预算 | max_tokens 收紧；judge 抽检 n ↓；生成模型换 flash |
