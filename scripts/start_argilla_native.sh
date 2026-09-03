#!/usr/bin/env bash
# Argilla 原生自建启动脚本（无 Docker）：
#   1) 启动 Elasticsearch（官方发行包自带捆绑 JDK，无需系统 Java）
#   2) 数据库迁移（首次）
#   3) 启动 Argilla 服务（uvicorn，端口 6900）
# 依赖：F:\无项目工作文件夹\tools\elasticsearch-8.17.0（解压官方 zip）
set -e
cd "$(dirname "$0")/.."

ES_HOME="/f/无项目工作文件夹/tools/elasticsearch-8.17.0"
ARGILLA_DB="sqlite:///$(pwd | sed 's|^/\([a-zA-Z]\)/|\1:/')/data/output/argilla.db"

echo "== 1/3 启动 Elasticsearch（后台，日志 es.log）=="
(cd "$ES_HOME" && nohup ./bin/elasticsearch.bat > "$(pwd)/data/output/es.log" 2>&1 &)

echo "== 等待 ES 就绪 =="
for i in $(seq 1 60); do
  if curl -s -m 3 http://127.0.0.1:9200 >/dev/null 2>&1; then echo "ES up"; break; fi
  sleep 3
done

echo "== 2/3 数据库迁移（首次）=="
ARGILLA_DATABASE_URL="$ARGILLA_DB" ./.venv/Scripts/python.exe -W ignore -c "
import pathlib
from alembic.config import Config
from alembic import command
cfg = Config(str(pathlib.Path('.venv/Lib/site-packages/argilla_server/alembic.ini')))
command.upgrade(cfg, 'head')
print('migration ok')
"

echo "== 3/3 启动 Argilla (http://127.0.0.1:6900, admin/distill123456, api_key=argilla.apikey) =="
ARGILLA_DATABASE_URL="$ARGILLA_DB" \
ARGILLA_AUTH_SECRET_KEY="super-llm-distill-gen-secret" \
ARGILLA_API_URL="http://127.0.0.1:6900" \
ARGILLA_WORKSPACE="admin" \
USERNAME="admin" PASSWORD="distill123456" \
ARGILLA_ENABLE_TELEMETRY="0" \
ELASTICSEARCH="http://127.0.0.1:9200" \
./.venv/Scripts/python.exe -W ignore -m uvicorn argilla_server:app --host 127.0.0.1 --port 6900
