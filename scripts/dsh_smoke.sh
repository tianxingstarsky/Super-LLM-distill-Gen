#!/usr/bin/env bash
# dsh 插件真机冒烟：在 deepseek-harness 里以 headless 模式让 agent 调用 dataforge 工具。
# 用法：bash scripts/dsh_smoke.sh "要 agent 完成的任务（中文）"
#   bash scripts/dsh_smoke.sh "用 dataforge 工具执行 models 命令，列出可用模型"
# 前置：harness 已 pnpm install + pnpm run build（见 README）；本机 node >= 22.19。
set -e
DF_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HARNESS="$DF_ROOT/components/deepseek-harness"

# C 盘纪律：pnpm 相关缓存与运行时状态一律放 F 盘工具目录
export COREPACK_HOME="${COREPACK_HOME:-F:/无项目工作文件夹/tools/corepack}"
export npm_config_store_dir="${npm_config_store_dir:-F:/无项目工作文件夹/tools/pnpm-store}"
export npm_config_cache="${npm_config_cache:-F:/无项目工作文件夹/tools/npm-cache}"
# CI=true：跳过 lefthook 装 git 钩子（submodule 的 worktree 配置限制，运行不需要）
export CI=true

# 插件调度环境
export DF_ROOT
export DF_PYTHON="$DF_ROOT/.venv/Scripts/python.exe"

# harness 运行态（会话/设置/凭据），不放仓库内、不放 C 盘
export DSH_HOME="${DSH_HOME:-F:/无项目工作文件夹/tools/dsh-home}"
export DSH_AGENTS_HOME="${DSH_AGENTS_HOME:-$DSH_HOME/.agents}"
export DSH_TELEMETRY_DISABLED=1
mkdir -p "$DSH_HOME"

# DeepSeek API key：从 gitignored 的 backends.local.yaml 读取，不硬编码、不入库
if [ -z "$DEEPSEEK_API_KEY" ]; then
  DEEPSEEK_API_KEY="$(grep -oP 'api_key:\s*\K\S+' "$DF_ROOT/configs/backends.local.yaml" | head -1 || true)"
fi
export DEEPSEEK_API_KEY
if [ -z "$DEEPSEEK_API_KEY" ]; then
  echo "缺少 DEEPSEEK_API_KEY（configs/backends.local.yaml 未配置 api_key）" >&2
  exit 2
fi

# 插件文件在仓库外，需要能解析 @deepseek-ai/* —— 复合依赖目录（junction）：
# plugins/dsh-dataforge/node_modules → tools/dsh-plugin-deps/node_modules
#   （内含 @deepseek-ai/cordis、@deepseek-ai/dsh-tools 指向 harness 工作区包）
DEPS_NM="$DF_ROOT/plugins/dsh-dataforge/node_modules"
if [ ! -e "$DEPS_NM/@deepseek-ai/cordis" ]; then
  node -e "
const fs = require('fs');
const T = 'F:/无项目工作文件夹/tools/dsh-plugin-deps/node_modules/@deepseek-ai';
const H = '$HARNESS'.replace(/\//g, '/');
for (const [name, dir] of [['cordis', 'vendor/cordis'], ['dsh-tools', 'packages/core/tools']]) {
  const link = T + '/' + name, target = H + '/' + dir;
  try { if (fs.existsSync(link)) fs.rmSync(link, { recursive: true }); } catch {}
  fs.symlinkSync(target, link, 'junction');
}
try { if (fs.existsSync('$DEPS_NM')) fs.rmSync('$DEPS_NM', { recursive: true }); } catch {}
fs.symlinkSync('F:/无项目工作文件夹/tools/dsh-plugin-deps/node_modules', '$DEPS_NM', 'junction');
"
  echo "已创建插件依赖 junction" >&2
fi

# .ts 插件加载：与源码入口一致（node --import tsx/esm）
export NODE_OPTIONS="${NODE_OPTIONS:+$NODE_OPTIONS }--import tsx/esm"

# 技能发现（用户级根，skill-filesystem 监视，无需重启）：
# $DSH_AGENTS_HOME/skills → 项目 skills 目录（dataforge + review-team）
AGENTS_SKILLS="$DSH_AGENTS_HOME/skills"
if [ ! -e "$AGENTS_SKILLS" ]; then
  mkdir -p "$DSH_AGENTS_HOME"
  node -e "
const fs = require('fs');
const [src, dst] = process.argv.slice(1);
fs.symlinkSync(src, dst, 'junction');
" "$DF_ROOT/plugins/dsh-dataforge/skills" "$AGENTS_SKILLS"
  echo "已联接技能目录 → $AGENTS_SKILLS" >&2
fi

# 补丁：本机绝对路径（make_dsh_patch.py 生成，gitignored）
PATCH="$DF_ROOT/plugins/dsh-dataforge/cordis.yml"
if [ ! -f "$PATCH" ]; then
  "$DF_ROOT/.venv/Scripts/python.exe" "$DF_ROOT/scripts/make_dsh_patch.py" >/dev/null
fi

cd "$HARNESS"
# 根脚本 dsh = node --import tsx/esm apps/cli/src/bin.ts（源码启动器，自带 .ts 加载）
# 第 2 个可选参数=团队补丁（审核小队/多个子智能体协作用）
TEAM_PATCH="${2:-}"
if [ -n "$TEAM_PATCH" ]; then
  exec corepack pnpm@11.7.0 run dsh --profile headless \
    --patch "$PATCH" --patch "$TEAM_PATCH" "$1"
fi
exec corepack pnpm@11.7.0 run dsh --profile headless --patch "$PATCH" "$1"
