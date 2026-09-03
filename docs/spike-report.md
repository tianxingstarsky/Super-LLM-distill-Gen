# M0 技术验证 spike 报告

日期：2026-09-03　环境：Windows 10 / Python 3.11.9 / Git Bash

## 真实 API spike（官方 DeepSeek V4 Flash，已跑）

可用模型：`deepseek-v4-flash` / `deepseek-v4-pro` / **`deepseek-v4-flash-vision-exp`**（视觉版，M1 多模态可用）。
实测成本：两轮完整运行合计 prompt 8891 / completion 10433 tokens（flash 价位，约几分钱）。

### 实测发现 R1：原版 Magpie 技巧在 chat API 上失效（重要配方变更）
空 user 回合**被 API 接受**，但输出是"助手打招呼"（模型在 chat API 里永远扮演助手角色），
10 条输出几乎完全相同——正是要防的模板重复。原版 Magpie"prompting with nothing"
依赖服务端 chat template 注入 pre-query 前缀（让模型续写用户回合），API 无此能力。
**新配方（已验证）**：`MAGPIE_QUERY_SYSTEM_PROMPT`（扮演好奇用户生成提问，
open-agentinstruct/UltraChat 指令多样化的标准做法）→ 实测 8/10 条高质量且高度多样
（月球轨道物理、梦境语言、图灵测试、医学不平衡数据集训练等）。已回写
`lib/adapters/distill_prompts.py` 与 `pipelines/build_magpie.py`。

### 实测发现 R2：DeepSeek V4 Flash 偶发空 completion（约 20%，temperature=1.0 时）
部分请求重试 3 次仍为空回复。**M1 必须内置**：空回复重试 + 失败条数记账 + 降温度兜底。
（脚本 `scripts/real_spike.py` 的 `chat()` 已示范重试逻辑。）

### 实测发现 R3：文本化三角色蒸馏在真实 API 上跑通
demo-chat-001 的 Generator 输出（节选）：
> 原计划快速用整体反转判断 → 发现这会额外分配空间，不满足"不额外分配空间"的约束 →
> 改用双指针从两端向中间逐个比较 → 成功。教训：方案必须先满足用户的显式约束，再考虑写法简洁。

Reflector 输出结构正确 JSON；"错误→教训、只留正确操作"的防呆约束在真实模型上成立。
样例存于 `data/output/spike_real/distill_samples.jsonl`（本地产物，不入库）。

### 实测发现 R4：云端 API 走系统代理正常
系统代理（127.0.0.1:18081）对 api.deepseek.com 转发正常、无请求体错乱；
错乱仅发生在 localhost 目标（F2 的 NO_PROXY 处理对云端无影响）。

## 结论速览

| 验证项 | 结果 | 说明 |
|---|---|---|
| V1 distilabel 1.5.3 Windows 跑通 + 步骤缓存 | ✅ | dry-run 1 次请求 / 冷跑 3 次 / 热跑 0 次，测试 `tests/test_pipeline_smoke.py` |
| V2 Magpie 链（无种子指令生成） | ✅ | 用内置 TextGeneration + 官方 system 模板 + 空 user 回合表达，`tests/test_magpie_chain.py` |
| V3 聊天日志蒸馏适配器 | ✅（离线） | chatlog→OpenCUA 同构轨迹 JSONL + 文本化三角色提示词，`tests/test_chatlog_adapter.py` |
| 全量离线单测 | ✅ 6/6 | 31 秒 |

## 关键发现（决定 M1 怎么做）

### F1. distilabel 1.5.3 在 Windows 可用，但有 4 个坑（已全部绕过）
1. **YAML 格式是完整序列化 dump**，不是声明式简写（`type:` 简写是 1.6 的新格式）。管线一律用 `pipelines/build_*.py` 构建生成 YAML。
2. **生成器步骤的 `data` 字段不序列化** → 自写 `LoadDataFromJSONL`（`lib/adapters/load_jsonl.py`）让管线可 CLI 直跑，同时是 M1 导入器雏形。
3. **秘密字段（api_key）不序列化** → 一律环境变量注入（与 `configs/backends.yaml` 的 `api_key_env` 一致）。
4. **dry_run 与 run 共用同一管线对象/缓存目录会互相污染**（批次管理器状态落盘）；每阶段用独立管线实例+独立 `_cache_dir`。另注意环境变量 `DISTILABEL_CACHE_DIR` 对本地管线不生效。

### F2.（重要，影响你的日常使用）本机 httpx 会把 localhost 请求转发给系统代理
本机 `http.proxy=127.0.0.1:18081`；httpx 默认 `trust_env` 会把 `127.0.0.1` 的请求也送进代理，代理改写请求体（实测请求体被加 `\r\n\r\n` 前缀、Content-Length 错位）→ LLM 调用静默失败。
**后果**：你以后连本地 Ollama/LM Studio 会莫名失败。**解决**：运行前设 `NO_PROXY=127.0.0.1,localhost`（`tests/conftest.py` 已示范；M1 的 CLI 会内置该处理，云端 API 不受影响）。
另：distilabel 解析输出依赖 `beautifulsoup4`，未随核心安装，已加进 requirements.txt。

### F6.（环境冲突，已避开）本机 8765 端口被你的 llama.cpp bridge_server 占用
排查测试失败时发现：`F:\llama_cpp\output_my_model\release\python\python.exe bridge_server.py`
监听 `127.0.0.1:8765`（疑似你的本地推理桥），同时你的 `train_pretrain.py --arch minimind4dpsk`
训练任务正在运行。**两者均未做任何改动**；本项目 mock 测试端口改为 **18765** 彻底避开。
后续 M1 如要接入你的 llama.cpp 本地端点，可把 `configs/backends.yaml` 里本地后端
base_url 指向 `http://127.0.0.1:8765/v1`（注意 NO_PROXY 处理，见 F2）。

### F3. Magpie 在 distilabel 1.5.3 只支持本地后端
内置 `MagpieGenerator` 仅挂载在 llamacpp/ollama/vLLM/HF 后端（需要 chat template 注入 pre-query 前缀），**OpenAILLM 不支持**。
API 适配方案（已实测变更，见上方 R1）：空 user 回合虽被接受但输出为助手打招呼（模板重复），
故改用 `MAGPIE_QUERY_SYSTEM_PROMPT`（扮演好奇用户生成提问），实测多样性良好。

### F4.（M1 的核心未知点，已给出应对）OpenCUA cot-generator 深度依赖截图
原版 Reflector 需要"前后截图 + 坐标补丁"、Generator 需要红圈标注截图 → **只适合带截图的 GUI 轨迹**（这类日志可直接走原版整链）。
**纯文本/工具调用日志**（你的"人与 agent 交互上下文"大概率是这类）走我们已落地的适配版：
- `lib/adapters/chatlog_to_traj.py`：对话/工具日志 → OpenCUA 同构轨迹 JSONL（`task_id` 确定性幂等、相邻重复去重、空轮跳过、超长截断；**运行时事实标记错误**：exit_code≠0/success=false；**用户纠正轮标记 feedback**；首条用户轮=instruction）。
- `lib/adapters/distill_prompts.py`：文本化 Reflector/Generator/Summarizer 三提示词（改编自上游结构，去除视觉依赖），内置正确性信号优先级（运行时事实 > 用户信号 > LLM 兜底）与反思防呆约束（错误只留一句教训 ≤20%、最终只含正确操作）。
- 离线测试已覆盖 schema/幂等/错误标记/防呆约束。

### F5.（Windows 缺陷，避免）自定义步骤持有 `llm` 字段会挂
自定义 GeneratorStep 声明 `llm: LLM` 字段后，worker 侧报 `TypeError: cannot pickle '_OverlappedFuture'`（Windows 事件循环对象不可序列化），管线永久挂起。**规避**：自定义步骤一律不持有 LLM；LLM 调用全部走内置任务（TextGeneration 等），自定义步骤只做纯数据变换（`RepeatGenerator`、`LoadDataFromJSONL`）。

## 变更记录（相对原计划）
- 删除自研 `MagpieAPIGenerator`（撞上 F5），改用内置 TextGeneration + 薄生成器（F3 方案）。
- OS-Atlas 仅保留"坐标格式参照"定位（其合成管线未开源）——与原计划一致。
- `DISTILABEL_CACHE_DIR` 环境变量不可靠 → M1 的 CLI 将直接管理 `_cache_dir`。

## 待用户侧完成的验证（依赖外部条件）
1. ~~真实 API key~~ ✅ 已提供 DeepSeek V4 Flash，真实 spike 已跑（见上）。
2. **本地端点**（Ollama/LM Studio）：验证 NO_PROXY 处理后链路可用。
3. **真实聊天/工具日志小样本**（放 `data/seeds/chatlogs/`，几十条即可、可脱敏）：在真实日志上跑通"chatlog→traj→文本化三角色蒸馏"全链（当前已用合成 fixture 在真实 API 上验证）。
4. 带截图的 GUI 轨迹样本（如有）：验证 OpenCUA 原版整链（需按 upstream 说明配置其 API key）。

## 附：已交付文件清单
- `lib/adapters/load_jsonl.py` / `repeat.py` / `chatlog_to_traj.py` / `distill_prompts.py`
- `pipelines/build_smoke.py`、`00_smoke.yaml`、`build_magpie.py`、`01_magpie.yaml`
- `tests/mock_llm_server.py`、`test_pipeline_smoke.py`、`test_magpie_chain.py`、`test_chatlog_adapter.py`、`conftest.py`
- `configs/backends.yaml`、`preferences.yaml`、`recipes/*.yaml`、`data/seeds/topics.txt`
- `requirements.txt`、`README.md`、`NOTICE`、`.gitignore`
