# CUDA 与本地 GPU 推理接入

本机：NVIDIA GeForce RTX 5060 Ti（16 GiB 显存），驱动 596.21。
**当前显存占用 15.8/16 GiB（≈99%）**——GPU 正被训练类任务占用；接入点已就绪，
但**现在不要在机器上启动本地模型**，避免抢占训练显存。

## 接入方式（早已支持，无需新代码）

DataForge 的所有 LLM 调用走 OpenAI 兼容网关（`lib/llm_client.load_backend`）：

```bash
# 方式 A：单次覆盖（任意命令）
df review-remote auto --base-url http://127.0.0.1:11434/v1 --model qwen2.5:7b-instruct
df distill --llm-check 5 --base-url http://127.0.0.1:11434/v1 --model qwen2.5:7b-instruct

# 方式 B：环境变量全局切换
LLM_BASE_URL=http://127.0.0.1:11434/v1 LLM_MODEL=qwen2.5:7b-instruct df review-remote auto

# 方式 C：配置文件常驻（gitignored backends.local.yaml 覆盖）
#   backends:
#     local_gpu:
#       base_url: http://localhost:11434/v1
#       models: [qwen2.5:7b-instruct]
#   model_roles:
#     judge: { backend: local_gpu, model: qwen2.5:7b-instruct }
#   这样 judge/审核/打分全部走本地 GPU，云端只剩大批量生成。
```

后端起任意 OpenAI 兼容服务：Ollama（`http://localhost:11434/v1`）、
vLLM（`http://localhost:8000/v1`）、llama.cpp server（`http://localhost:8765`）。
`df doctor` 会探测 GPU 存在、显存占用、torch+cuda 与三类本地端点。

## 何时用本地 GPU（与训练共存规则）

1. **显存占用 <90%**：可起小模型（7B 级 Q4 约 5-6 GiB）跑 judge/审核/精炼——零成本、
   免预算、离线可用；大模型给云端。
2. **显存占用 ≥90%**：doctor 出提醒；此时启动本地模型会 OOM 或拖垮训练任务，
   一律改用云端（DeepSeek V4 系）。
3. 训练期把 `model_roles.judge` 指回云端，训练结束再切回 local_gpu。
4. 本地端点返回缓慢或空回复时（Ollama 默认低温度下 Qwen 偶发空 completion），
   `chat_json` 会重试；仍不稳就把该角色切回云端——质量门不降级。

## 我们这边为何“没动 CUDA”

- 数据工厂的计算全部在模型推理：蒸馏、判分、翻译、多模态、Agent 模拟——都是
  LLM 调用，CUDA 只在“本地跑模型”时发挥；
- 本项目自身的 Python 侧只做格式转换/去重（sha256）与 SQLite 读写，无矩阵计算；
- 因此接入 CUDA=接入本地推理端点，而不是给管线加 CUDA 代码；上游 distilabel 的
  vLLM/Ollama 步骤同样从这些端点出。

## 立即可用的本地链（显存空闲后）

1. 安装 Ollama 并拉起：`ollama serve` + `ollama pull qwen2.5:7b-instruct`；
2. `df doctor` 应显示 GPU <90% 与“本地推理端点：Ollama（:11434）”；
3. `df models --base-url http://127.0.0.1:11434/v1` 列出本地模型；
4. 小成本验证：`df review-remote auto --base-url http://127.0.0.1:11434/v1 --model qwen2.5:7b-instruct --batch 3`
   ——本地 GPU 判 3 条，中心照常审计身份+理由。
