# 审核持久化

`training_review_driver.py` 复用训练内容审核的持久化流程；SFT、CPT 和偏好适配器绑定各自规则。`agent_review_driver.py` 保存 Agent 决定和审计事件；`agent_driver.py` 将运行任务视角的端口转换到该存储接口。

两个 Agent 类职责不同：Adapter 适配调用合同，Driver 拥有存储行为。不要在两处复制审核规则。发布任务继续使用旧审核发布服务，保持来源校验与锁语义。

任何写入都要核对当前候选版本。不要信任浏览器缓存的“已通过”状态，也不要直接改生成文件以表现审核结果。
