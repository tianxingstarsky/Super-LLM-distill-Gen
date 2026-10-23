# 业务域

| 业务域 | 一致性对象 | 对外入口 |
| --- | --- | --- |
| [workflows](workflows/README.md) | 固定配方的一次运行、尝试、阶段进度 | `api.py` |
| [datasets](datasets/README.md) | 训练记录、证据、清单组成的可交付版本 | `api.py` |
| [review](review/README.md) | 与候选身份、来源哈希绑定的审核决定 | `api.py` |
| [models](models/README.md) | 服务配置、能力声明和探测证据 | `api.py` |
| [documents](documents/README.md) | 文档内容、保留结构的分块、解析来源证据 | `api.py` |

每个目录中的 `domain/` 放纯规则，`application/` 放用例与消费方端口，`infrastructure/` 放外部实现。`contracts.py` 只导出需要跨域共享的纯合同。其他域不能导入内部文件；组合根例外，因为它负责选择实现。

新增目录时同时写 README，交代职责、入口、依赖和验证方式。不要为每个页面建立业务域，也不要把多个业务域共有的内容直接堆入一个新的 `utils.py`。
