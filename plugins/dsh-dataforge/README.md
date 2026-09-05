# DataForge 插件（deepseek-harness）

把 Super-LLM-distill-Gen 的 df-* 数据管线暴露为 harness 工具：模型（或用户）
在 dsh 对话里直接说"把文档转成训练数据"，agent 调 `dataforge` 工具执行对应管线。

**已真机验证（headless 模式，2026-09）**：agent 调用 `dataforge` 执行
`models`（`python -m lib.cli models`，返回后端 3 个模型）与
`gate status`（G0/G1/G3 真实状态）两条命令，输出与实际一致。

## 前置：harness 仓库构建

```bash
cd components/deepseek-harness
# pnpm 请用 corepack 安装并固定 11.7.0；缓存全部放 F 盘（C 盘纪律）：
#   export COREPACK_HOME=F:/无项目工作文件夹/tools/corepack
#   export npm_config_store_dir=F:/无项目工作文件夹/tools/pnpm-store
#   export npm_config_cache=F:/无项目工作文件夹/tools/npm-cache
# CI=true：跳过 lefthook 装 git 钩子（harness 是 submodule，core.worktree 在公共 config 里，装不了；运行不需要钩子）
# 构建依赖时 PATH 需能解析 pnpm（Windows 下用 F:/无项目工作文件夹/tools/df-bin/pnpm.cmd 垫片，或 corepack enable）
CI=true corepack pnpm@11.7.0 install --frozen-lockfile
CI=true corepack pnpm@11.7.0 run build
```

## 安装

```bash
# 1. 生成机器相关的补丁文件（make_dsh_patch.py 把插件名写成 file:// URL——
#    插件名必须是带 scheme 的 URL，Windows 绝对路径原生 import() 不认）
python scripts/make_dsh_patch.py

# 2. 设置环境变量
export DF_ROOT="F:/无项目工作文件夹/Super-LLM-distill-Gen"
export DF_PYTHON="$DF_ROOT/.venv/Scripts/python.exe"   # Windows；Linux/macOS 指向 venv/bin/python

# 3. 一键冒烟（自动准备插件依赖 junction + tsx loader + 环境）
bash scripts/dsh_smoke.sh "用 dataforge 工具执行 models 子命令，列出可用模型"

# 4. 手动启动（web 为例）；根脚本 dsh 是源码启动器（自带 node --import tsx/esm，能加载 .ts 插件）
cd components/deepseek-harness
corepack pnpm@11.7.0 run dsh web --patch "$DF_ROOT/plugins/dsh-dataforge/cordis.yml"
```

## 踩坑记录（真机联调总结）

- **插件名 URL**：cordis 的 `insert.name` 必须是 `file:///…`（loader 只对 `./`、`../`
  相对路径自动转 URL；Windows `F:/…` 原样传给 `import()` 会报
  `ERR_UNSUPPORTED_ESM_URL_SCHEME`）。
- **插件依赖解析**：pnpm workspace 的 `@deepseek-ai/cordis` 只按消费包逐包链接，
  仓库**根** node_modules 里没有它。插件在仓库外，必须自备依赖：冒烟脚本会给
  `plugins/dsh-dataforge/node_modules` 建 junction → `tools/dsh-plugin-deps/node_modules`
  （内含 `@deepseek-ai/{cordis,dsh-tools}` 指回 harness 工作区包）。
- **CI=true**：harness 是 submodule，root postinstall 的 lefthook 装钩必失败，
  `CI=true` 让安装器跳过（仅影响 git 钩子，不影响运行）。
- **位置参数**：`gate` / `review` / `review-remote` 的 action 是位置参数不是
  `--action`；插件用 `POSITIONAL_OPS` 映射（有契约测试防漂移）。
- **`.ts` 插件加载**：走 `node --import tsx/esm`（根脚本 `dsh` 已是），
  直接跑构建产物 `apps/cli/lib/bin.js` 时无 tsx 注册器，需 `NODE_OPTIONS=--import tsx/esm`。

## 插件组成

- `src/dataforge.ts`：Cordis 插件（name/inject/apply + defineTool），调度
  `python -m lib.cli <command>`；命令表与 lib/cli.py 子命令交叉校验
  （tests/test_dsh_plugin.py），防止两边漂移。
- `skills/dataforge/SKILL.md`：给 agent 的工作流说明（何时用哪条命令、
  闸门铁律、预算与模型），可复制到 harness 的 skill 目录由 skill-filesystem 发现。
- `cordis.yml`：由 make_dsh_patch.py 生成的补丁（file:// URL + 本机绝对路径，
  不入库；cordis.example.yml 为模板）。

## 安全设计

- 所有数据智能都在 Python 管线（已过 97 项测试与真机评测）；插件零智能、只调度。
- 闸门（G0/G1/G3）在 CLI 层硬拦截；插件描述与 skill 都要求 agent 先 gate status。
- 输出上限 8MB；非零退出码时 stderr 一并返回给模型（isError 语义由上游处理）。
- DeepSeek API key 只从 gitignored 的 configs/backends.local.yaml 读取（见 dsh_smoke.sh），
  不硬编码、不入库；harness 会话/凭据放在 `DSH_HOME`（F 盘，不在仓库内）。
