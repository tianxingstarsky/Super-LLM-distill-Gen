# DataForge 插件（deepseek-harness）

把 Super-LLM-distill-Gen 的 df-* 数据管线暴露为 harness 工具：模型（或用户）
在 dsh 对话里直接说"把文档转成训练数据"，agent 调 `dataforge` 工具执行对应管线。

## 安装

```bash
# 1. 生成机器相关的补丁文件（绝对路径写入 cordis.yml）
python scripts/make_dsh_patch.py

# 2. 设置环境变量
export DF_ROOT="F:/无项目工作文件夹/Super-LLM-distill-Gen"
export DF_PYTHON="$DF_ROOT/.venv/Scripts/python.exe"   # Windows；Linux/macOS 指向 venv/bin/python

# 3. 在 deepseek-harness 仓库根启动（web 为例）
cd components/deepseek-harness
pnpm dsh web --patch "$DF_ROOT/plugins/dsh-dataforge/cordis.yml"
```

## 插件组成

- `src/dataforge.ts`：Cordis 插件（name/inject/apply + defineTool），调度
  `python -m lib.cli <command>`；命令表与 lib/cli.py 子命令交叉校验
  （tests/test_dsh_plugin.py），防止两边漂移。
- `skills/dataforge/SKILL.md`：给 agent 的工作流说明（何时用哪条命令、
  闸门铁律、预算与模型），可复制到 harness 的 skill 目录由 skill-filesystem 发现。
- `cordis.yml`：由 make_dsh_patch.py 生成的绝对路径补丁（不入库；cordis.example.yml 为模板）。

## 安全设计

- 所有数据智能都在 Python 管线（已过 82 项测试与真机评测）；插件零智能、只调度。
- 闸门（G0/G1/G3）在 CLI 层硬拦截；插件描述与 skill 都要求 agent 先 gate status。
- 输出上限 8MB；非零退出码时 stderr 一并返回给模型（isError 语义由上游处理）。
