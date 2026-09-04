#!/usr/bin/env bash
# 一键拉起全部本地服务（幂等：已在运行的跳过）。
# 启动顺序：Redis → Elasticsearch → Argilla（原生自建）→ 静态预览服务器 → 控制台
set -e
cd "$(dirname "$0")/.."
PY=".venv/Scripts/python.exe"   # Windows venv

start_if_down() {
  local name="$1" port="$2" cmd="$3"
  if curl -s -m 3 "http://127.0.0.1:$port" >/dev/null 2>&1 || [ "$name" = "redis" ] && "$PY" -c "import redis;redis.Redis(host='127.0.0.1',port=$port).ping()" >/dev/null 2>&1; then
    echo "✓ $name 已在运行"
    return
  fi
  echo "▶ 启动 $name …"
  eval "$cmd"
}

# 1) Redis（无持久化）
start_if_down redis 6379 '
  (cd /f/无项目工作文件夹/tools/redis-win && nohup ./redis-server.exe --bind 127.0.0.1 --port 6379 --save "" --appendonly no >/dev/null 2>&1 &)'

# 2) Elasticsearch（捆绑 JDK）
start_if_down elasticsearch 9200 '
  (cd /f/无项目工作文件夹/tools/elasticsearch-8.17.0 && nohup ./bin/elasticsearch.bat >/dev/null 2>&1 &)'

# 等 ES 就绪（最多 60 秒）
for i in $(seq 1 20); do
  curl -s -m 3 http://127.0.0.1:9200 >/dev/null 2>&1 && break
  sleep 3
done
curl -s -m 3 http://127.0.0.1:9200 >/dev/null 2>&1 || echo "⚠ ES 未就绪（稍后手动检查）"

# 3) Argilla（原生自建；迁移幂等）
start_if_down argilla 6900 '
  ARGILLA_DATABASE_URL="sqlite:///F:/无项目工作文件夹/Super-LLM-distill-Gen/data/output/argilla.db" \
  ARGILLA_AUTH_SECRET_KEY="super-llm-distill-gen-secret" \
  ARGILLA_API_URL="http://127.0.0.1:6900" \
  ARGILLA_WORKSPACE="admin" USERNAME="admin" PASSWORD="distill123456" \
  ARGILLA_ENABLE_TELEMETRY="0" ELASTICSEARCH="http://127.0.0.1:9200" \
  ARGILLA_REDIS="redis://127.0.0.1:6379/0" \
  (nohup "$PY" -W ignore -m uvicorn argilla_server:app --host 127.0.0.1 --port 6900 >/dev/null 2>&1 &)'

# 4) 静态预览服务器（data/output → 18700）
start_if_down preview 18700 '
  (nohup "$PY" -m http.server 18700 --bind 127.0.0.1 --directory data/output >/dev/null 2>&1 &)'

# 5) 控制台（8501）
start_if_down console 8501 '
  (nohup "$PY" -W ignore -m lib.cli console >/dev/null 2>&1 &)'

echo "== 服务状态 =="
for svc in "Argilla:6900" "控制台:8501" "预览:18700" "ES:9200"; do
  IFS=':' read -r name port <<< "$svc"
  code=$(curl -s -m 3 -o /dev/null -w "%{http_code}" "http://127.0.0.1:$port" || echo 000)
  echo "  $name($port) → $code"
done
"$PY" -c "import redis;print('  Redis(6379) →', 'UP' if redis.Redis(host='127.0.0.1',port=6379).ping() else 'DOWN')" 2>/dev/null || echo "  Redis(6379) → DOWN"
