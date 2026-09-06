# 无看门狗的单服务进程

Windows 双击 `scripts/start_all.vbs`，或执行 `python -m lib.cli console`。
推荐启动器持有单实例文件锁，不轮询、不定时重启、不拉起 JVM。

- Streamlit UI：127.0.0.1:8501。
- 审核 API 线程：127.0.0.1:6900。
- 两个监听端口属于同一服务 PID；生成任务按需使用隐藏子进程。
- 原 Redis / Elasticsearch / Argilla 服务已经停用；历史数据文件没有删除。
- `review-server` 是无需 UI 的独立部署选项，不要与 console 重复绑定同一个端口。
- 后台子进程设置 CREATE_NO_WINDOW，dsh Node 子进程设置 windowsHide，不引入看门狗。
- 配置或端口冲突会报错退出，不自动杀掉占用端口的其他应用。

运行态、日志与缓存使用项目输出目录或同盘 tools 目录。服务默认只监听回环地址。
控制台没有互联网身份认证，不应直接暴露公网；远程审核走经过 TLS/SSH 保护的 API 接入。

SQLite 使用 WAL。在线备份应调用 SQLite backup API，不能把“只拷贝主 db 文件”当作一致性备份保证。
更多工作流程与边界见 [quality-and-operations.md](quality-and-operations.md)。
