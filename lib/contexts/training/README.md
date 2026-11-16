# 本地训练验证

负责固定数据与模型快照下的原模型、SFT、CPT→SFT 对照，保存预检、训练、生成评测及报告。
它是可选下游能力，不修改来源数据、既有生成任务、缓存或导出合同。

入口 `api.py`；`bootstrap/training_validation.py` 装配文件端口和成熟训练器。
应用层只协调可恢复阶段，领域层约束单次遍历、监督完整性与题目隔离。
Transformers、PEFT、量化及 CUDA 只在可选基础设施执行时加载。
训练依赖单独安装在训练环境，不进入桌面安装包和普通数据读取路径。

目录说明见各层 README，命令入口为 `scripts/training/validate_local.py`。
验证覆盖单次遍历约束、完整推理与 system、结束标记、失败阶段不冒充完成及恢复不重复训练。

`RunGrpoValidation` 提供独立的可选 TRL GRPO 对照：固定可验证任务、原模型评测、
最多一轮的策略优化与同题留出评测。`df train-grpo` / `scripts/training/validate_grpo.py`
显式启动，默认预检；额外依赖见 `requirements-grpo.txt`。数值正确奖励与推理质量分开说明。
当前仅完成单步运行与离线接线验证，32K 端到端尚未完成；不作为稳定训练功能或效果提升宣传。
维护与使用见 [本地 GRPO 验证](../../../docs/local-grpo-validation.md)。
