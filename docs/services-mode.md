# 单进程融合架构（无 Redis/ES/Argilla/看门狗）

曾经的 collab 栈（Redis + Elasticsearch + Argilla + 静态预览 + 控制台 + 看门狗，
~2.5GB+，看门狗 20 秒一轮探测、服务冷启动被打断时黑窗频闪）已全部下线，
**全部能力融合进一个进程**：

```
python -m lib.cli console          # 双击 scripts/start_all.bat 同效
├── Streamlit 控制台 (8501)        # 全部页面：总览/资产/预览/运行/审核/监控/闸门/偏好
└── 审核中心线程 (6900)            # SQLite 存储 + 协作 HTTP API + /files/ 静态预览
    （协作者在自己主机：df review-remote pull/auto/human/submit）
```

| 原组件 | 融合后的归宿 |
|---|---|
| Redis / Elasticsearch (JVM) | 删除——审核数据存 SQLite（`data/review_center.db`，WAL 并发） |
| Argilla 服务端 | 删除——`lib/review_center.py`：用户/记录/响应 + 内置 HTTP API（纯标准库） |
| 静态预览 http.server | 融合——审核中心 `/files/<path>` 直接服务 data/output |
| 看门狗（黑窗元凶） | 删除——单进程无需守护；服务随控制台启停 |
| 建号脚本 setup_argilla_user.py | 删除——`df user create <用户名> [--role admin|annotator]` |

## 为什么这样更商业

- **运维面最小**：一个进程、一个 SQLite 文件，备份=拷贝一个文件；没有 JVM/双数据库
  的版本与内存问题（原栈 ~2.5GB+，现在一个 python 进程 ~300-400MB）；
- **没有重启风暴**：没有看门狗就没有"20 秒打断冷启动→黑窗频闪"这类故障模式；
  控制台挂了自己重启（`start_all.bat`）即可；
- **协作者协议不变**：`df review-remote` 三步（pull/auto/human/submit）与身份+理由
  审计全部保留，只是底层从 Argilla SDK 换成标准库 HTTP（`lib/review_center.py`）；
- **边界诚实**：中心机单点=控制台进程（如需 7×24 独立 API，`df review-server --port 6900`
  独立跑，同款代码同款 SQLite）；SQLite 适合中小规模批次（数千条记录级），
  若未来单批审核规模到十万级再评估专用评审平台。

## 常用命令

```bash
python -m lib.cli console              # 一键全栈（UI+API）
python -m lib.cli review-server        # 仅审核中心 API（无 UI 的部署）
python -m lib.cli user create zhang    # 发协作者账号（每人唯一 api_key）
python -m lib.cli user list
df review-remote pull/auto/submit      # 协作者端（见 docs/collaboration.md）
```

审核数据与工作区关系：`records/responses` 表带 dataset 列（`rollout_review` /
`rollout_review_<工作区>`），与 docs/workspaces.md 的分区语义完全一致。
