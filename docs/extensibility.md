# 操作扩展指南

## 怎样加一条新数据管线（六步，全部对照单一事实源）

1. **实现**：`lib/你的模式.py`（复用 lib.llm_client 的 chat_json / lib.prompts 资产库、
   防重复 manifest），提示词先入 `lib/prompts/`（版本/出处/约束声明）。
2. **注册**：`configs/pipelines/commands.yaml` 加一行 —— 这是**单一事实源**，
   控制台管线运行页和 CLI 契约测试由它派生；外部控制器消费独立的版本化协议。
3. **接 CLI**：`lib/cli.py` 加子命令（`add_parser` + `cmd_xxx`）；闸门用
   `_gates().require(...)`。控制台会自动出现在"管线运行"页（无需改 webapp）。
4. **测试**：`tests/test_xxx.py`（fake client 全链路；核心断言：结果正确性+闸门路径），
   并确认 `tests/test_console.py::test_command_registry_single_source_of_truth` 仍绿
   （该测试校验 CLI ⊆ 注册表）。
5. **真机评测**：提示词过 `df prompt-eval --ids 你的前缀`；再跑小批量 `df 你的命令`。
6. **外部控制器**：通过 `python -m lib.control` 的 `dataforge.control` v1 协议接入。能力扩展保持向后兼容；破坏性变化升级协议主版本。dsh 适配独立安装、独立测试和发布，参见 [适配包](../plugins/dsh-dataforge/README.md)。

## 怎样加新控制台页面

`lib/webapp.py` → PAGES dict 加一项（函数签名 `def page_xxx() -> None`，
复用 lib 逻辑；无其他改动）。

## 怎样换模型/端点

- 角色槽位：`configs/backends.yaml → model_roles`（generation/judge/vision/
  refine/simulate/translation 六槽）。
- 单次覆盖：各命令 `--backend/--model`；或环境变量 `LLM_BASE_URL/LLM_MODEL`。
- 查模型：`df models --backend X` 或 `--base-url URL`（/v1/models 网关自动获取）。

## 怎样加新审核维度

审核页与审核中心的"标签问题"在各自代码中定义（webapp 的 keep/reject 为一对）；
扩展为多标签时同步改 `lib/review.py` 的 build_records/dataset settings 与
`pull_decisions` 的响应解析即可。
