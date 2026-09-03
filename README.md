# Super-LLM-distill-Gen

面向 LLM 大模型训练集生成的超级简化工具：**基于已发布的开源组件组合，把"聊天记录 / 图片 / 截图 / 主题种子"蒸馏成 SFT、DPO、多模态、翻译与操作智能体训练数据。**

## 原则

1. **不发明智能体**：所有智能功能来自上游已发布组件（submodule 锁版本）；本项目只写适配器（格式转换）、闸门状态机与配置。
2. **HITL（人在回路）**：所有可变操作（预算/数据源/偏好/放量/导出…）经用户确认，agent 只提议与执行已确认动作。
3. **不写界面**：人工审核界面用 Argilla，监控用 Langfuse，交互基底用 deepseek-harness（插件后置）。
4. **偏好=软配比**：用户偏好作用于数据配比与 recipe 采样，绝不硬注入指令文本（防重复输出）。
5. **版本化与审计**：清洗/增强只写新版本不覆盖；回流前审计日志前置；跑批前 spotcheck 预检。

## 流程对齐（商用验证）

蒸馏主链与百度千帆"数据飞轮"、阿里百炼"日志回流"同构：
日志 → 正负分离（点赞/纠正/执行结果） → 负样本用强模型重写 → 清洗（脱敏/相似去重/毒性过滤） → 增量训练数据 → 评估门（新模型打赢旧模型才上线）。

## 组件对照（组件 ↔ 功能 ↔ 论文）

| 组件（锁定 commit） | 承担功能（上游已验证） | 论文依据（arXiv 已核实） |
|---|---|---|
| distilabel 1.5.3 | 管线引擎：YAML DAG、步骤缓存/断点、UltraFeedback 判分、分组 chosen/rejected、MinHash/embedding 去重、Magpie 任务、OpenAI 兼容+Ollama/vLLM/LiteLLM | UltraFeedback 2310.01377（ICML 2024） |
| components/magpie（MIT, b734a36） | 无种子指令生成模板、FAISS 去重、unitag 打标、多轮对话 | Magpie 2406.08464（ICLR 2025）；去重 2107.06499（ACL 2022）；SemDeDup 2303.09540 |
| components/opencua（MIT, dfc91ba） | 聊天蒸馏主链：data-processor（动作规约/状态匹配）+ cot-generator（Reflector 标错→Generator 反思 CoT→Summarizer 打分）；逐窗口生成防爆上下文 | OpenCUA/AgentNet 2508.09123；Reflexion 2303.11366（NeurIPS 2023）；STaR 2203.14465（NeurIPS 2022）；Lost in the Middle 2307.03172（TACL 2024） |
| Langfuse（自托管，M2） | 监控：trace/token 成本/dashboard | —（LLM 原生可观测，替代 MLflow） |
| Argilla 2.8（Docker，M1 审核用） | HITL 人工审核界面（distilabel 判分建议自动注入） | MT-Bench judge 偏差 2306.05685（NeurIPS 2023，人工门控依据） |
| deepseek-harness（M2 插件接入） | 交互基底：CLI/TUI、会话、工具权限 | — |
| LLaMA-Factory / MS-Swift | 最终数据格式规范（sharegpt+images、chosen/rejected、messages+images） | — |

其余论文依据：DPO 2305.18290（NeurIPS 2023）、RLAIF 2309.00267（ICML 2024）、Self-Refine 2303.17651（NeurIPS 2023）、Data Mixing Laws 2403.16952（ICLR 2025）、Cambrian-1 2406.16860（NeurIPS 2024）、TAGCOS 2407.15235（NAACL 2025 Findings）、LLaVA-DPO 2309.14525、DeepSeek-R1 2501.12948、Qwen3 2505.09388、Bactrian-X 2305.15011、MADLAD-400 2309.04662、SeaLLMs 2312.00738、OS-Atlas 2410.23218、GUICourse 2406.11317、OmniParser 2408.00203（V2 仅技术报告）、LLMLingua 2310.05736（EMNLP 2023）、MemGPT 2310.08560（arXiv preprint）、LongAlign 2401.18058（EMNLP Findings 2024）。

## 目录结构

```
Super-LLM-distill-Gen/
├── README.md  LICENSE(Apache-2.0)  NOTICE
├── components/            # submodule 锁版本（magpie, opencua）
├── pipelines/             # distilabel 管线（YAML）
├── lib/
│   ├── adapters/          # 纯格式转换（每段对照上游格式文档）
│   ├── gates.py           # HITL 闸门状态机（M1: G0/G1/G3）
│   ├── prefs.py           # 偏好软采样+校正
│   ├── dedup.py           # MinHash + sha256 manifest
│   └── cli.py             # df-* 命令
├── configs/               # backends.yaml / preferences.yaml / recipes/ / gates.yaml
├── data/                  # seeds/(chatlogs, images, screenshots, topics.txt)  output/(shards, reports, manifest.jsonl)
├── docker/                # Langfuse / Argilla 自托管说明（M1 起）
├── plugins/dsh-dataforge/ # deepseek-harness 插件（M2）
└── tests/                 # 离线单测（fixture=组件公开样例）
```

## 快速开始（M0 阶段）

```bash
# 0. 依赖
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt   # distilabel==1.5.3

# 1. 拉取子模块
git submodule update --init --depth 1

# 2. 配置模型后端（API key 用环境变量注入，见 configs/backends.yaml）
#    OpenAI 兼容即可：DeepSeek / DashScope / Ollama / LM Studio / vLLM 服务

# 3. 离线冒烟（不需要任何 key，验证管线+缓存机制）
python -m tests.mock_llm_server &          # 127.0.0.1:8765 的 mock OpenAI 服务
.venv/Scripts/python -m distilabel pipeline run --config pipelines/00_smoke.yaml

# 4. 真实小预算生成（需要 key 或本地端点）
#    df-run 链与 df-distill 链见 docs/ 与 spike 报告
```

## 当前状态

- [x] M0 环境：仓库克隆、submodule 锁定、distilabel 1.5.3 安装
- [x] M0 验证 1：distilabel 最小管线（Windows、缓存）✅
- [x] M0 验证 2：Magpie 链（无种子指令生成，API 适配版）✅
- [x] M0 验证 3：OpenCUA 蒸馏链适配器（chatlog→traj + 文本化三角色，离线）✅
- [x] M0 spike 报告：见 docs/spike-report.md
- [ ] M0（依赖你）：真实数据 spike——需 API key/本地端点 + 日志小样本
- [ ] M1：最小闭环（CLI + 三闸门 + 偏好 v1 + 导出 + Argilla/Langfuse）
- [ ] M2：dsh 插件、其余闸门、多模态/DPO 增强/翻译/GUI 管线

⚠️ 本机使用注意：httpx 会把 localhost 请求送进系统代理导致 LLM 调用静默失败，
运行前需 `NO_PROXY=127.0.0.1,localhost`（详见 docs/spike-report.md F2）。

## 许可证

本项目 Apache-2.0。第三方组件许可证见 NOTICE 与 components/*/LICENSE。
