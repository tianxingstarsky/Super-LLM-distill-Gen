# 训练验证规则

`validation.py` 定义实验配置、确定性摘要、题目划分和完整监督合同。
`complete_reasoned_generation` 定义本次开启思考的验证输出何时完整；`assessment_usability` 从评分、已确认矛盾与完整输出推导报告指标。未知保持未知，不由外部 JSON 的自报通过字段决定。
每个训练分支只遍历其训练单元一次；CPT→SFT 为先 CPT 后 SFT 的联合有序一轮。
不读取文件、模型、CUDA 或训练框架，不根据训练 loss 推断任务正确率。
