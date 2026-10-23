# 本地辅助脚本

`launch_console.py`、`launch_desktop.py` 启动产品；`import_rollout.py` 导入轨迹；`make_gui_fixture.py` 准备受控展示数据；`real_spike.py` 用于真实场景验证。

[promo](promo/README.md) 是宣传片工具，[assets](assets/README.md) 是脚本使用的本地资产。产品业务规则不能放进这些脚本再由页面反向导入。

真实任务可能访问外部服务，执行前核对脚本配置和本次授权。日志、密钥、用户资料与模型权重不得提交。Windows 后台启动保持隐藏窗口，并明确进程所有权。
