# 审核持久化

`training_review_driver.py` 复用训练内容审核的持久化流程；SFT、CPT 和偏好适配器绑定各自规则。`agent_review_driver.py` 保存 Agent 决定和审计事件；`agent_driver.py` 将运行任务视角的端口转换到该存储接口。

两个 Agent 类职责不同：Adapter 适配调用合同，Driver 拥有存储行为。不要在两处复制审核规则。发布任务继续使用旧审核发布服务，保持来源校验与锁语义。

任何写入都要核对当前候选版本。不要信任浏览器缓存的“已通过”状态，也不要直接改生成文件以表现审核结果。

自动评审维护入口：

| 文件 | 责任 |
| --- | --- |
| `automatic_adapter.py` | 连接候选、集合和返工用例；认证父版本、流式分组、CoT/SFT 谱系对齐与发布 |
| `automatic_runtime.py` | 兼容运行时钩子、来源读取、逐轮/逐句评审与实际修正请求 |
| `review_protocol.py` | 历史模型返回 shape、格式错误诊断与重试协议文字 |
| `review_style.py` | 从当前冻结节点配置解析风格，不凭旧候选自报决定规则 |

`WorkflowReviewRepair` 的旧类名作为运行时接入接口保留；评分修复主循环由 `ReviewCandidate` 拥有。调用仍走原 `ask`，维持共享并发、取消、流式输出和检查点身份。禁止把用户资料、原始供应商错误或密钥带入格式错误反馈。
