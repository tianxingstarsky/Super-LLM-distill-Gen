# 训练验证用例

`RunTrainingValidation` 先预检，依次执行原模型评测、SFT 训练与评测、CPT→SFT 训练与评测。
通过 `TrainingValidationPort` 复用完成收据，成功保存后才推进下一阶段。
预检不通过时不加载训练权重；训练和评测异常由文件端口保留已产生的证据。
用例不打开文件、不导入训练 SDK、不创建进程或修改既有数据集。

`full_sft.py` 中的 `RunFullSft` 支持单独预检和单遍全语言主干训练。组合根注入样本准备和训练端口，命令入口为 `df train-sft`。

`grpo.py` 的 `RunGrpoValidation` 通过 `GrpoValidationPort` 协调原模型、
TRL 策略训练、训练后留出评测；支持只预检、只跑原模型与完整恢复。
它不计算优势、不拥有奖励模型或优化器，不向既有 SFT 用例增加 RL 分支。
显式容量续训先由文件边界认证父任务和检查点，沿用已消耗进度；用例不自行重置 epoch。
