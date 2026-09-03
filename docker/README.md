# Docker 服务（M1 第四批）

| 服务 | 地址 | 用途 |
|---|---|---|
| Argilla（人工审核） | http://localhost:6900（admin / distill123456） | G3 放量闸的审核界面：逐条"保留/驳回" |
| Langfuse（监控） | http://localhost:3210（本机 3000 被占用，映射到 3210） | 管线 trace、token 成本、dashboard |

启动（需 Docker Desktop 运行）：
```bash
docker compose -f docker/argilla.yml up -d
docker compose -f docker/langfuse.yml up -d
```

⚠️ 已知网络问题（2026-09-03 实测）：本机 Docker 引擎无法直连 Docker Hub
（registry-1.docker.io TLS 握手超时），镜像拉取失败。解决方式（任选）：
1. 开启代理/VPN 后重试；或
2. Docker Desktop → Settings → Resources → Proxies 配置代理；
   或 Settings → Docker Engine 增加 registry-mirrors 后 Restart。
   （注意：重启 Docker Desktop 会短暂中断你正在运行的 openmymodel 容器，
    请自行选择时机；本工具不会替你重启 Docker。）

服务可用后：
1. `df review push`：把预览样本推送到 Argilla（带 judge 评分建议）
2. 在 Argilla 界面人工标注 keep/reject
3. `df review pull`：拉回标注，写 review.jsonl；通过率 ≥ 阈值（默认 90% 且 ≥10 条）
   时自动把 G3 闸门置为 approved（放量依据）
4. Langfuse 监控：在 backends.local.yaml 配 langfuse 段（public_key/secret_key/host，
   首次访问 http://localhost:3210 注册后在 Settings 里创建 API keys）；
   未配置时监控自动降级为本地 runs.jsonl 审计记录。
