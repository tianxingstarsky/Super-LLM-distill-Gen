# 原生 CoT 的 Beta 前缀续写

## 用途和实际调用

已有原生推理、原作者会话和审查意见时，返工 v8 使用 DeepSeek 的 Chat Prefix Completion。
`ContinueNativeFeedback` 分派到 `ContinueNativePrefix`，后者把原评审作为 system 提醒，
通常一次调用原作者续写推理并交付替换答案。不先请求读取反馈工具，不购买独立评审。
若正常 STOP 仅返回了新增推理，新任务允许复用该检查点再做一次答案前缀收尾；不是重跑推理。

实际消息末尾为：

```json
{
  "role": "assistant",
  "content": "",
  "prefix": true,
  "reasoning_content": "完整原推理，加上本任务冻结的可选反思起点"
}
```

模型传输通过 `reasoning_prefix_mode="reasoning"` 局部切换
`https://api.deepseek.com/beta/chat/completions`。普通请求和旧 `answer` 前缀模式不变。
共享客户端、账号、节点绑定、准入、流式记录、结算和检查点继续复用既有设施。

官方合同见 [前缀续写](https://api-docs.deepseek.com/zh-cn/guides/chat_prefix_completion/)
及 [reasoning_content 参数](https://api-docs.deepseek.com/api/create-chat-completion/)。
接口接受前缀不证明一定产生新的推理，也不认证最终答案正确。

## 适用范围与来源

新任务只有在全部活动原生作者均固定到官方 DeepSeek Chat 时才冻结
`native_feedback_guidance_version=8`。其他供应商和缺少认证端点的配置保持 v7；旧任务不热改。
本次真实验证只使用上游模型列表确认的 V4.1-Flash。

原资料、真实历史和用户发言不重写。审查意见继续是 system 提醒，不是用户的新发言或已证实事实。
真实工具历史和工具定义不能删除后伪装成无工具请求；这种来源明确拒绝走本路径，不偷偷降级。

原推理逐字保留。模型已经写完的推理常以最终交付意图收尾，直接传回可能立即输出答案。
新建 v8 任务因此单独冻结 `native_prefix_reflection_seed`，在完整原推理后提供一个开放的反思起点。
它不规定思考步骤、长度或结论，也不改写评审；缺少该字段的冻结任务不补默认值。

收据把原生原稿、程序提供的反思起点、本次上游返回的推理及答案分开保存。
`reasoning_segments` 中的程序片段明确为非原生来源，组合后的正文不冒充一次全新模型响应。
原推理里的历史缺陷仍在存档中，不能把“后续已纠正”描述成“旧内容已删除”。

## 完成、失败与恢复

正常结束且具备新增推理与答案，才完成此续写合同；完成仍为 `not_assessed`。
新任务冻结 `native_prefix_finalize_empty_answer=true`：只有已认证的 STOP、非空新增推理、
空答案才能进入一次 `content="\n"` 的答案前缀收尾；最多一次推理阶段和一次答案阶段。
`execute_from_incomplete` 可从已有完整失败收据继续，只购买答案阶段。截断、无推理、
已有答案或不完整来源均不能触发这一步，第二阶段失败后不循环。
不增加独立评审，也不把未抽检的重答写成质量通过。已有本地来源、结构、角色和导出检查保留。
再次显式返工保留完整父收据与分段来源，不能将先前程序添加的起点重新标为模型原生推理。

上游返回空推理、空答案、截断或拒绝时，保留实际响应、消息和检查点，并记录未完成原因。
已完成但缺少答案的付费响应仍落盘；恢复同一检查点不能再次购买它。
变更反思起点或已有意见会产生新的明确请求身份，不能覆盖先前尝试或隐藏已付费用。

完整实际发包由 HTTP 观察器记录最终 JSON 正文、URL 和摘要，不记录认证头。
构造阶段的 `request_messages` 只用于协议认证，不能代替最终 HTTP 证据。

## 验证入口

- `test_reasoning_prefill.py`：真实 SDK 的最终路径、前缀、模式、流式及错误证据。
- `test_native_prefix_feedback.py`：已有意见绑定、原文保留、片段来源及收据认证。
- `test_native_prefix_adapter.py`：原作者、共享结算、检查点恢复及失败不重购。
- `test_native_prefix_generation.py`：实际生成与 SFT/CoT 投影、重复显式返工、无新增评审。
- `test_ddd_architecture.py` 与受影响的原生返工回归：依赖和历史协议兼容。

真实尝试及消耗以独立报告展示；离线协议验证不能替代真实语义质量结论。

## 本次真实结果与未解决项

复用了两条历史英文对话的原作者、完整原生推理和已存评审。上游模型列表确认所用
deepseek-flash 对应 DeepSeek-V4.1-Flash-1017；原记录和历史来源恢复记录不改。

| 分支 | 样本 | 调用 | 新增推理 tokens | 实际结果 | 耗时 | 估算 USD |
| --- | --- | ---: | ---: | --- | ---: | ---: |
| 直接复用原推理 | 01 | 1 | 0 | 有答案，缺新增推理 | 3.281 s | 0.001085100 |
| 直接复用原推理 | 02 | 1 | 0 | 有答案，缺新增推理 | 4.016 s | 0.000905154 |
| 加显式反思起点 | 01 | 1 | 531 | 新推理和新答案完整 | 7.094 s | 0.000803790 |
| 加显式反思起点 | 02 | 1 | 1242 | 正常 STOP，但没有答案 | 7.875 s | 0.000786588 |
| 仅补全 02 答案 | 02 | 1 | 0 | 立即 STOP，两个通道为空 | 1.094 s | 0.000227988 |

五次实际请求合计 23.360 秒，输入 23,510 tokens（缓存命中 13,440），输出 3,763 tokens
（含推理 1,773）。按当时的[官方非高峰价格](https://api-docs.deepseek.com/quick_start/pricing/)
估算 USD 0.003808620，包含诊断和失败，没有独立评审或读取反馈工具调用。
未核对账户扣款，也不是每条合格样本的成本；合同完成不等于语义评审通过。

01 修正了备用输出必须预先准备的依赖。02 在新推理中考虑了 OCR 的可选性，答案通道仍缺失，
不得把推理里的成稿段落挪作答案。本次没有继续循环购买，历史推理中的控制文字仍完整保留。

初次存档缺少完成字段。02 的收尾使用一份派生收据：先核对完整实际流式记录的 STOP、
推理、正文及实际请求摘要，再只补入观测到的终止原因，原记录不改，依据单独存档。
当前前缀设施直接保存真实 finish_reason，不猜造完成状态。

[官方转换源码](https://github.com/deepseek-ai/deepseek-recipe/blob/main/deepseek-recipe/src/protocol/openai/chat_completion/request/convert.rs)
将空 content、非空 reasoning 的前缀识别为推理续写；
[V4.1 渲染器](https://github.com/deepseek-ai/deepseek-recipe/blob/main/deepseek-recipe-encoding/src/v4/dsv41.rs)
在此条件下不补思考结束标记。本次空答案不能简单归因于空字符串字段错误，也未证明上游故障。
完整报告、实际 HTTP JSON 和原始响应保存在本地运行产物中，不提交运行资料。
