# 本地辅助脚本

开源运行时调整只验证受影响路径，见 `docs/architecture/open-source-runtime-2026-10-26.md`。已撤回的框架迁移及当时的对照脚本存于本地 `.dataforge/oss-migration/withdrawn-langgraph/`，不作为当前实现或验收结果。

`replay_reasoning_environment.py --output <目录>` 仅回放 `tests/fixtures/reasoning_agent/environment_sessions.json`，输出完整问答、推理、工具回执和新旧对照 HTML/JSON。脚本禁止网络连接，不创建模型客户端；新协议字段投影明确标记，不能将它的程序检查通过率当作真实模型正确率。验证见 `tests/test_reasoning_environment.py`。

`launch_console.py`、`launch_desktop.py` 启动产品；`import_rollout.py` 导入轨迹；`make_gui_fixture.py` 准备受控展示数据；`real_spike.py` 用于真实场景验证。

[promo](promo/README.md) 是宣传片工具，[assets](assets/README.md) 是脚本使用的本地资产。产品业务规则不能放进这些脚本再由页面反向导入。

真实任务可能访问外部服务，执行前核对脚本配置和本次授权。日志、密钥、用户资料与模型权重不得提交。Windows 后台启动保持隐藏窗口，并明确进程所有权。
