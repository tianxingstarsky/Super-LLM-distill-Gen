#!/usr/bin/env bash
# 启动 dsh 本体 Web UI（挂 dataforge 插件），并打印带 token 的入口地址。
#   bash scripts/dsh_web.sh [port]     # 默认 3080
# 说明：dsh web 有浏览器信任栅栏——必须用日志里带 ?token=… 的地址打开；
#       进程分离运行（关闭终端不影响），日志在 $DF_ROOT/../tools/dsh-web.log。
set -e
DF_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${1:-3080}"
TOOLS="$DF_ROOT/../tools"
LOG="$TOOLS/dsh-web.log"

"$DF_ROOT/.venv/Scripts/python.exe" - "$DF_ROOT" "$PORT" <<'PY'
import os, pathlib, subprocess, sys, yaml
root = pathlib.Path(sys.argv[1]); port = sys.argv[2]
harness = root / "components" / "deepseek-harness"
tools = root.parent / "tools"
env = dict(os.environ)
env.update({
    "DF_ROOT": str(root), "DF_PYTHON": str(root / ".venv" / "Scripts" / "python.exe"),
    "DSH_TELEMETRY_DISABLED": "1",
    "DSH_HOME": str(tools / "dsh-home"), "DSH_AGENTS_HOME": str(tools / "dsh-home" / ".agents"),
    "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
    "PYTHONIOENCODING": "utf-8",
})
cfg = yaml.safe_load((root / "configs" / "backends.local.yaml").read_text(encoding="utf-8"))
env.setdefault("DEEPSEEK_API_KEY", cfg["backends"]["deepseek"]["api_key"])
log = (tools / "dsh-web.log").open("a", encoding="utf-8", buffering=1)
argv = ["node", "--import", "tsx/esm", "apps/cli/src/bin.ts", "--profile", "web",
        "--patch", str(root / "plugins" / "dsh-dataforge" / "cordis.yml"),
        "--no-open", "--port", port]
p = subprocess.Popen(argv, cwd=str(harness), env=env, stdout=log, stderr=log,
                     stdin=subprocess.DEVNULL, creationflags=0x00000008 | 0x00000200)
print(f"dsh web 启动中（pid {p.pid}）…")
PY
for _ in $(seq 1 30); do
  sleep 2
  URL="$(grep -o 'http://127.0.0.1:[0-9]*/?token=[A-Za-z0-9_-]*' "$LOG" | tail -1 || true)"
  [ -n "$URL" ] && break
done
if [ -n "$URL" ]; then
  echo "入口（必须带 token）：$URL"
else
  echo "未在日志中发现 token 入口，请查看 $LOG"
  exit 1
fi
