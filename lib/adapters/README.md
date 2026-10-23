# 管线格式适配

这里的 adapter 是 CLI 数据管线的输入输出格式，不是 DDD 业务域的存储端口实现。

`rollout_import.py`、`chatlog_to_traj.py` 导入智能体与对话记录；`load_jsonl.py`、`distill.py`、`distill_prompts.py`、`repeat.py` 提供已有管线步骤；`opencua_out.py` 连接对应导出格式。

保留来源、角色、工具调用和对话完整性。格式转换不能擅自丢弃推理、重排工具消息或把不完整轨迹标成成功。新业务规则放入领域层，适配器只负责协议和字段映射。
