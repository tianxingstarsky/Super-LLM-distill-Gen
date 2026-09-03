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

## 🌏 全中文界面（已内置）

上游 Argilla 2.8 仅带 de/en/es/ja 语言包。我们已自建中文包并重建前端：
- 中文语言包：`scripts/assets/zh.js`（en.js 全量 300+ 文案翻译 + 上游漏译键 Workspaces 补译）
- 构建替换脚本：`bash scripts/build_argilla_zh.sh`（克隆 v2.8.0 前端源码 → 注入 zh →
  Nuxt2 构建（Node≥18 需 NODE_OPTIONS=--openssl-legacy-provider）→ 备份原 static →
  替换 → 重启服务；含回滚命令）
- 默认语言 = 中文；页面右上角用户菜单 → 我的设置 → 语言 可切回英文。
- 注意：构建产物替换的是当前 venv 内的 static，重装 argilla-server 后需重跑脚本。
- 原版英文界面备份在 F:\无项目工作文件夹\tools\argilla-static-backup-orig。

## ⚠️ 磁盘纪律（C 盘满的教训，2026-09-03）

- 所有数据/缓存必须在 F 盘：`source scripts/env_redirect.sh`（PIP_CACHE_DIR、HF_HOME
  重定向到 F:\无项目工作文件夹\tools\）；ES/Redis/Argilla sqlite 均在 F 盘。
- pip 缓存曾在 C 盘堆积 5.2GB（已清）；`~/.cache/huggingface` 8.8GB 是既有模型缓存（未动）。
- 任何新组件接入前先确认其数据/缓存路径落在 F 盘。
