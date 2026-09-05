# 服务运行模式（系统资源与功能的取舍）

## 三档模式（`python -m lib.services mode <模式>`，单进程默认）

| 模式 | 运行进程 | 内存 | 功能 |
|---|---|---|---|
| **light（默认）** | 控制台（Streamlit 单进程） | ~300MB | **全部**：数据操作/审核/监控/资产/偏好/闸门——都在控制台内 |
| **share** | light + 静态预览页 | ~330MB | 额外：可分享的只读预览链接 |
| **collab** | share + Redis + ES(JVM) + Argilla | ~2.5GB+ | 额外：Argilla 多人协作审核平台（团队标注场景才需要） |

```bash
python -m lib.services mode light    # 切换到单进程模式（默认）
python -m lib.services mode collab   # 切换到完整协作栈
python -m lib.services start         # 启动看门狗（守护当前模式的服务，自动重启下线者）
python -m lib.services status        # 查看模式/内存提示/健康状态
```

## 为什么默认单进程

- ES/Redis/Argilla 三个服务只为"多人协作审核"这一个功能服务——单人使用完全用不上；
- 控制台已内置全部数据操作与审核流程（保留/驳回/放行 G3）；
- 需要团队审核时再 `mode collab`（Argilla 中文界面，后台 ES/Redis 自动拉起）；
- 看门狗按模式只守护对应服务，轻量模式下**不会**自动拉起重型栈。

## 服务护栏

- 看门狗以 pythonw 分离进程运行（脱离会话存活），每 20 秒探测→下挂自动重启；
- 单个服务每小时最多自动重启 3 次（防止崩溃循环烧资源）；
- 完整协作栈初始化为幂等（迁移/建号均可重复执行，见 scripts/start_services.sh）。
