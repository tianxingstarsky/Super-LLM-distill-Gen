# 训练验证用例

`RunTrainingValidation` 先预检，依次执行原模型评测、SFT 训练与评测、CPT→SFT 训练与评测。
通过 `TrainingValidationPort` 复用完成收据，成功保存后才推进下一阶段。
预检不通过时不加载训练权重；训练和评测异常由文件端口保留已产生的证据。
用例不打开文件、不导入训练 SDK、不创建进程或修改既有数据集。
