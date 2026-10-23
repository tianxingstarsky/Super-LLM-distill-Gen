# 审核

处理 SFT、CPT、偏好对与 Agent 轨迹的人工审核。审核决定必须绑定候选身份及来源版本，修订发布保留证据，不能覆盖原始生成产物。

公开入口为 [api.py](api.py) 的四类审核应用。组合根位于 `lib/bootstrap/*_reviews.py` 和 `agent_review.py`。

本域的“人工审核应用”与生成运行时的“自动评分修正算法”不是同一实现。后者仍在 `workflow_review_repair.py`，迁入时必须保留其检查点和模型调用合同。

参阅 [application](application/README.md)、[domain](domain/README.md)、[infrastructure](infrastructure/README.md)。验证应覆盖过期来源、重复提交、审核发布和多轮推理字段保留。
