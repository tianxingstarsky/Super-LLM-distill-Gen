# 训练加载示例

| 想做什么 | 入口 | 结果 |
| --- | --- | --- |
| 检查同一份 JSONL 如何进入 Qwen3.5 | `preview_qwen35.py` | 逐助手轮的 token 数、监督范围、适配器摘要，可选完整文本 |
| 选择目标的原生思考开关映射 | `qwen35-thinking-on.json` / `qwen35-low-off.json` | 默认全部开启，或仅将 `low` 作为关闭思考的读取视图 |
| 标记 DeepSeek 的固定控制自述 | `--source-control-profile deepseek-v4.1-flash-control/v1` | 已确认的字面串变为可替换标记，原数据保持不变 |
| 在自己的训练器中复用加载器 | [训练强度转换说明](../../docs/training-reasoning-control.md) | 惰性读取与保留 labels 的接入示例 |

这里仅有文件读取和示例组合。训练控制规则归 datasets domain；逐轮转换归 datasets application；模型模板和 tokenization 归 datasets infrastructure，经 `lib.bootstrap.datasets` 装配。产品页面不能反向依赖本目录。

从仓库根目录运行，路径换成已有文件和本地 tokenizer 目录：

```powershell
.venv/Scripts/python.exe -X utf8 scripts/training/preview_qwen35.py --input path/to/sft.jsonl --tokenizer path/to/qwen35-tokenizer --take 2 --show-text
```

旧记录没有 `training_control`，或 `by_message` 缺少某个助手轮时，只有明确选择的 `--default-effort high` 才会补充本次读取视图。这不会改写文件，也不能证明旧样本生成时使用过该强度。`--max-length 32768` 会拒绝超长样本，不截掉推理或答案。

`--effort-mapping scripts/training/qwen35-low-off.json` 在读取时把 `low` 转为 `enable_thinking=False`。默认五档都映射为 `True`。关闭思考的视图使用空 think 块和答案，原记录的推理仍完整保留。映射只写原生开关，不向 system 追加五档说明，也不要求目标模型另学五档。

确认输入来自 DeepSeek V4.1-Flash 后，可启用 `--source-control-profile deepseek-v4.1-flash-control/v1`。
它只标记助手推理中已确认的 `Desired oververbosity 5` 和 `desired verbosity 5`，
生成 `[[SJ_OUTPUT_VERBOSITY:5]]`。另传 `--verbosity-reference-text "输出详尽程度：5"`
可在读取时改为指定文字；这只是文字替换示例，不是 Qwen 的原生控制参数。
源位置、替换记录和摘要随预览返回，不把 verbosity 的 5 当成思考强度。
其它模型、其它表达与数值保持原样；任务引用相关文字时拒绝歧义转换。

依赖为已有项目 Python 环境、Transformers 和本地 Qwen3.5 tokenizer/template。脚本不下载权重、不启动训练、不调用模型 API、不写转换数据集。当前适配范围是文本 SFT 和多轮对话；思考开启时目标轮必须有推理。工具、媒体及不支持的角色不能静默降级。

验证应使用真实 tokenizer 检查开关映射、推理／答案监督范围、角色边界和 `-100` 提示掩码，并运行本次训练控制与 DDD 回归。成功预览只证明预处理可运行，不是训练后质量结论。
