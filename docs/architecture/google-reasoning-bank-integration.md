# Google ReasoningBank 原版接入

采用 [Google 官方完整仓库](https://github.com/google-research/reasoning-bank)，固定 `ed80611788292ea739f1effd31f16c53823b8a0d`，Apache-2.0。源码位于 `components/reasoning-bank`。

| 阶段 | 实际执行的官方代码 | 必要适配 |
| --- | --- | --- |
| 单轨迹学习 | `WebArena/induce_memory.py::main/format_trajectory`、原成功/失败模板 | 输入真实对话与已有推理；模型走节点网关 |
| 已有同任务多稿对照 | `WebArena/induce_scaling.py::main`、`PARALLEL_SI` | 不自动买多稿；显式处理上游重复路径、倒置标签及客户端返回差异 |
| 检索 | `WebArena/memory_management.py::load_cached_embeddings/l2_normalize/screening/select_memory` | 原 JSONL 缓存格式；本地 FastEmbed 多语种供应商 |
| 收集与采用 | 既有个人经验库、工作流配方 | 完整原记录、版本、采用与停用；新任务冻结实际选中文本 |

研究脚本导入时会初始化云客户端。宿主校验整文件及函数摘要，加载并执行未改动的原函数定义，显式注入真实模型和轨迹 I/O；不是仿造 SDK，也不是上游 CLI 的零配置启动。回执保存实际源文件、函数摘要、提示词与响应。Windows I/O 显式使用 UTF-8，空格路径不被拆分。

## 安装

```bash
git submodule update --init components/reasoning-bank
python -m pip install -r requirements-reasoning-bank.txt
```

首次准备本地模型：

```python
from pathlib import Path
from fastembed import TextEmbedding
TextEmbedding("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    cache_dir=str(Path.home() / ".shujian-cube/cache/reasoning-bank"))
```

之后只读本地模型。依赖不可用时明确显示，允许不带记忆继续，不把关键词匹配冒充语义嵌入。

## 使用

入口在节点「提示词与风格 → 长期经验 → 从轨迹进化风格」。一次建议评审的反馈先持久化，点击 Google 学习才提炼一份；恢复使用已付费响应。无结果观测不伪造成功或失败；模型评语只作为未独立验证的 `autoeval`。

候选可查看、编辑、采用、停用及载入旧版。采用后原版检索选出的文字与来源随新任务冻结，通过既有公开经验 system 进入原生 CoT 和阅读对话；导出保留实际输入。恢复初始版本会停用检索。旧任务保持不变。

程序入口 `lib.bootstrap.google_reasoning_bank.google_reasoning_bank_application` 提供 `learn/activate/freeze`；原版对照入口是 `OfficialReasoningBankInductor.contrast`，只接受同任务真实备选，不自动追加采样。

## 评审和交付

进化接收方是生成角色：SFT 答案生成、原生推理与同次答案、多轮等。评审仅提供意见和反例，既不作为学习目标，也不接收自动检索的生成经验。旧存储中即使存在 review 选择，新任务也会忽略它。评审模板仍可由用户手动配置。

新任务只做一次建议评审。拒绝、未决或评语格式错误都保留为观察，语义结果不再触发返工和重复评审，实际结果按策略输出。`released_advisory` 不等于质量通过。来源绑定损坏、无实际内容、必需推理缺失及文件损坏仍单独报告。思考强度文字与标记可选；零命中不阻断，原文保留。

## 验证范围

V4.1-Flash 已通过原 `main` 提炼两条真实经验，由原 `select_memory/screening` 配合本地 384 维多语种向量召回。缓存恢复不增加调用。这证明链路工作，不证明整体质量已经提高。MaTTS 的宿主协议仅离线验证，未购买额外多稿实跑。

Chrysalis 完整源目录也已保存。它是 SRE 参考工程，包含缓存与流量控制示例；未运行的云服务、热补丁及拓扑演化不计为数据生成能力。
