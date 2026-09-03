# M0 技术验证 spike 报告

日期：2026-09-03　环境：Windows 10 / Python 3.11.9 / Git Bash　成本：¥0（全部走本地 mock 端点，无 API 调用）

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

### F3. Magpie 在 distilabel 1.5.3 只支持本地后端
内置 `MagpieGenerator` 仅挂载在 llamacpp/ollama/vLLM/HF 后端（需要 chat template 注入 pre-query 前缀），**OpenAILLM 不支持**。
API 适配方案（已验证跑通）：内置 `TextGeneration` + Magpie 官方 system 模板 + 空 user 回合（"prompting with nothing" 的 chat 消息等价物），模板取自 `components/magpie/configs/model_configs.json`。
**待真实端点验证**：部分 API 拒绝空 user content（DeepSeek/Qwen 需实测）；若拒绝，退路是 user 内容放单个空格/短引导词（`lib/adapters/repeat.py` 的模板可配）。

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
1. **真实 API key**（`configs/backends.yaml` 的任一后端）：空 user 回合是否被接受（F3 待验证点）。
2. **本地端点**（Ollama/LM Studio）：验证 NO_PROXY 处理后链路可用。
3. **真实聊天/工具日志小样本**（放 `data/seeds/chatlogs/`，几十条即可、可脱敏）：跑通"chatlog→traj→文本化三角色蒸馏"全链，产出 20–50 条真实样例。
4. 带截图的 GUI 轨迹样本（如有）：验证 OpenCUA 原版整链（需按 upstream 说明配置其 API key）。

## 附：已交付文件清单
- `lib/adapters/load_jsonl.py` / `repeat.py` / `chatlog_to_traj.py` / `distill_prompts.py`
- `pipelines/build_smoke.py`、`00_smoke.yaml`、`build_magpie.py`、`01_magpie.yaml`
- `tests/mock_llm_server.py`、`test_pipeline_smoke.py`、`test_magpie_chain.py`、`test_chatlog_adapter.py`、`conftest.py`
- `configs/backends.yaml`、`preferences.yaml`、`recipes/*.yaml`、`data/seeds/topics.txt`
- `requirements.txt`、`README.md`、`NOTICE`、`.gitignore`
