# 人工审核服务：两条路径

## 主路径：原生自建（无 Docker，已实测全链路）

Argilla 2.8 服务端有 PyPI 包（argilla-server），配合本地 Elasticsearch + Redis 即可原生运行：

| 组件 | 获取方式 | 说明 |
|---|---|---|
| argilla-server | pip install argilla-server==2.8.0 | FastAPI 服务端（uvicorn 直跑，绕开其 CLI 的 typer/click 兼容问题） |
| Elasticsearch 8.17 | 官方 zip（artifacts.elastic.co）解压到 F:\无项目工作文件夹\tools\ | **自带捆绑 JDK，无需系统 Java**；config 已配单节点+关闭安全 |
| Redis | tporadowski Redis-x64-5.0.14（GitHub release） | Windows 原生；**redis-py 必须锁 4.6.0（RESP2）**，5.0 不支持 RESP3 HELLO |
| SQLite | 无依赖 | Argilla 数据库（migrations 需跑一次） |

启动：
```bash
# 1. ES（后台）
cd /f/无项目工作文件夹/tools/elasticsearch-8.17.0 && ./bin/elasticsearch.bat
# 2. Redis（后台，无持久化）
cd /f/无项目工作文件夹/tools/redis-win && ./redis-server.exe --bind 127.0.0.1 --port 6379 --save '' --appendonly no
# 3. 建号（幂等，admin/distill123456，api_key=distill.apikey）
ARGILLA_DATABASE_URL="sqlite:///F:/无项目工作文件夹/Super-LLM-distill-Gen/data/output/argilla.db" \
  .venv/Scripts/python.exe scripts/setup_argilla_user.py
# 4. 起服务（scripts/start_argilla_native.sh 可串起以上流程）
bash scripts/start_argilla_native.sh
```
使用：`df review push`（推样本+judge 建议）→ Argilla UI 标注（http://127.0.0.1:6900，
admin/distill123456）→ `df review pull`（拉回答，通过率 ≥90% 且 ≥10 条自动放行 G3）。

## 可选路径：Docker（多机部署/升级便利，需先解决本机 Docker Hub 网络）

| 服务 | 地址 | 用途 |
|---|---|---|
| Argilla（人工审核） | http://localhost:6900（admin / distill123456） | G3 放量闸的审核界面 |
| Langfuse（监控） | http://localhost:3210（本机 3000 被占用，映射到 3210） | 管线 trace、token 成本、dashboard |

```bash
docker compose -f docker/argilla.yml up -d
docker compose -f docker/langfuse.yml up -d
```

⚠️ 已知网络问题（2026-09-03 实测）：本机 Docker 引擎无法直连 Docker Hub
（registry-1.docker.io TLS 握手超时）。解决方式（任选）：
1. 开启代理/VPN 后重试；或
2. Docker Desktop → Settings → Resources → Proxies 配置代理；
   或 Settings → Docker Engine 增加 registry-mirrors 后 Restart。
   （重启会中断正在运行的容器，请自行选择时机。）

## ⚠️ 磁盘纪律（C 盘满的教训，2026-09-03）

- 所有数据/缓存必须在 F 盘：`source scripts/env_redirect.sh`（PIP_CACHE_DIR、HF_HOME
  重定向到 F:\无项目工作文件夹\tools\）；ES/Redis/Argilla sqlite 均在 F 盘。
- pip 缓存曾在 C 盘堆积 5.2GB（已清）；`~/.cache/huggingface` 8.8GB 是既有模型缓存（未动）。
- 任何新组件接入前先确认其数据/缓存路径落在 F 盘。
