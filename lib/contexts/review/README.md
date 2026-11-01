# 审核

处理 SFT、CPT、偏好对与 Agent 轨迹的人工审核，以及生成候选的自动评分、修复和复核。审核决定必须绑定候选身份及来源版本，修订发布保留证据，不能覆盖原始生成产物。

公开入口为 [api.py](api.py) 的人工审核应用、`ReviewCandidate`、`ReviewGeneratedCollections` 和 `ExecuteRepairBranch`。组合根位于 `lib/bootstrap/*_reviews.py`、`agent_review.py`、`automatic_review.py`。跨域配置校验从 [contracts.py](contracts.py) 导入。

`ControlTextSuggestionApplication` 另提供同次评审的控制文字匹配建议。建议与评分分开，
只在节点确认保存后采用；装配入口为 `lib/bootstrap/review_control_text.py`。

人工审核与自动评审共享候选身份原则，仍有各自用例。自动循环已经迁入 application；基础设施负责版本化模型协议、来源认证和落盘。旧 `workflow_review_repair.py` 是模块身份别名，不再拥有第二套实现。

参阅 [application](application/README.md)、[domain](domain/README.md)、[infrastructure](infrastructure/README.md)。验证应覆盖过期来源、重复提交、审核发布和多轮推理字段保留。
