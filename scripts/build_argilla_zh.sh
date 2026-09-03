#!/usr/bin/env bash
# Argilla 界面全中文：构建中文前端并替换服务器静态资源（可复现/可回滚）。
# 前置：已按 docker/README.md 原生自建 Argilla；本机有 Node/npm。
# 流程：1) 克隆 v2.8.0 前端源码（稀疏）  2) 注入 zh 语言包与默认语言
#       3) 安装依赖 + 构建  4) 备份服务器原 static 后替换  5) 重启服务
set -e
cd "$(dirname "$0")/.."

TOOLS="/f/无项目工作文件夹/tools"
SRC="$TOOLS/argilla-src"
VENV_STATIC=".venv/Lib/site-packages/argilla_server/static"
BACKUP="$TOOLS/argilla-static-backup-$(date +%Y%m%d-%H%M%S)"
TAG="v2.8.0"

echo "== 1/5 拉取前端源码（$TAG，稀疏） =="
if [ ! -d "$SRC" ]; then
  git clone --depth 1 --branch "$TAG" --filter=blob:none --sparse https://github.com/argilla-io/argilla.git "$SRC"
  (cd "$SRC" && git sparse-checkout set argilla-frontend)
fi

echo "== 2/5 注入中文语言包 =="
FE="$SRC/argilla-frontend"
cp scripts/assets/zh.js "$FE/translation/zh.js"
python - <<'PY'
from pathlib import Path
p = Path(r'F:\无项目工作文件夹\tools\argilla-src\argilla-frontend\nuxt.config.ts')
t = p.read_text(encoding='utf-8')
if 'code: "zh"' not in t:
    t = t.replace('''      {
        code: "ja",
        name: "日本語",
        file: "ja.js",
      },
    ],''', '''      {
        code: "ja",
        name: "日本語",
        file: "ja.js",
      },
      {
        code: "zh",
        name: "中文",
        file: "zh.js",
      },
    ],''')
    t = t.replace('defaultLocale: "en"', 'defaultLocale: "zh"')
    p.write_text(t, encoding='utf-8')
    print('nuxt.config.ts 已注入 zh')
else:
    print('zh 已存在，跳过')
PY

echo "== 3/5 安装依赖并构建（Nuxt2 + Node24 需 legacy openssl） =="
(cd "$FE" && export NODE_OPTIONS=--openssl-legacy-provider && npm ci --no-audit --no-fund && npx nuxt generate)

echo "== 4/5 备份并替换服务器静态资源 =="
if [ -d "$VENV_STATIC" ]; then
  mkdir -p "$(dirname "$BACKUP")" && cp -r "$VENV_STATIC" "$BACKUP"
  echo "原版已备份到: $BACKUP"
fi
rm -rf "$VENV_STATIC"/* 2>/dev/null || true
cp -r "$FE/dist/"* "$VENV_STATIC/"
echo "static 已替换"

echo "== 5/5 重启 Argilla（后台任务会中断，请用 start_argilla_native.sh 重启） =="
echo "完成。重启服务：bash scripts/start_argilla_native.sh"
echo "回滚：cp -r $BACKUP/* $VENV_STATIC/"
