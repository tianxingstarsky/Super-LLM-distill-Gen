# 本地 GRPO 验证

**实验入口，尚未完成端到端验证。** 已实际执行 1 个优化器步并保存原生检查点，
对应 0.125ep；这一组奖励相同、梯度为零，不能作为学习有效的证据。
32K 容量的基线与续训验证尚未完成，没有可发布的训练前后效果结论。
本入口不进入正常生成工作流或桌面安装包；当前产品优化仍以数据制作流程为主。

这是可选的下游训练能力，使用 Hugging Face **TRL `GRPOTrainer`** 更新 PEFT 适配器。
不是 RLAIF 文件导出，也不是自写的强化学习算法。当前提供命令行与 HTML 报告，尚未加入工作流画布、桌面安装包或分布式训练服务。

## 安装与启动

复用独立 CUDA 训练环境，安装 `requirements-grpo.txt`。其中额外固定 `trl==1.18.0`；
PyTorch CUDA wheel 按显卡选择，不能把这些依赖安装进普通桌面环境。
模型目录使用本机已有的完整 Qwen3.5 权重、tokenizer、官方模板及有效生成配置。

```powershell
.venv-training/Scripts/python.exe -m pip install -r requirements-grpo.txt
.venv-training/Scripts/python.exe scripts/training/validate_grpo.py --model path/to/qwen35-model --output .dataforge/grpo-validation --task-source gsm8k --train-size 8 --evaluation-size 8 --max-completion-length 32768 --action preflight
.venv-training/Scripts/python.exe scripts/training/validate_grpo.py --model path/to/qwen35-model --output .dataforge/grpo-validation --task-source gsm8k --train-size 8 --evaluation-size 8 --max-completion-length 32768 --action run
```

已安装项目 CLI 的环境也可使用 `df train-grpo`，参数相同。
默认操作只是预检。`baseline` 只生成训练前留出结果，`run` 按顺序执行全部阶段，
`report` 重建已取得结果的报告。继续执行时，必须使用同样的模型、目录与完整配置。

## 任务、奖励与对照

默认输出容量为 **32,768 token**，不是要求模型写满。题量默认 32／24；
上面的首次本机验证示例显式收敛为 8 道训练题和 8 道留出题。
`--task-source gsm8k` 使用固定版本 `openai/gsm8k` 的原始 train／test 划分，
按固定种子独立抽取题目；原始行号、来源版本与 MIT 许可证随清单保存。
首次显式预检可能通过 Hugging Face datasets 下载这一小型公开题库，后续可复用缓存。
参考数值从原始答案的 `####` 末行提取；原始解题说明不会喂给模型。
这是少量公开题目的工程验证，基座可能已见过题库，不能代表完整 GSM8K 基准、
通用推理、论文知识掌握或百万级生产效果。

默认的 `--task-source synthetic` 可完全离线构造精确分数、全程平均速度、集合、
整除计数和存量变化任务。它们共享任务模板但具体输入不重合。此来源便于检查接线，
合成分数题可能诱发长时间验算；真实训练应先预检输出容量和奖励方差。

模型收到中性的 system 消息与问题，使用官方聊天模板开启思考。
参考答案单独保存，只提供给奖励函数，不进入生成提示。奖励复用 TRL 官方
`accuracy_reward` 与 Math-Verify，不维护自己的答案提取语法或符号求解器，也不调用模型评审。
`Fraction` 只在来源边界核对 GSM8K 标准答案是否合法，不负责评判模型输出。

问题只追加 `Put your final numerical answer in \boxed{}.`，不强加终止行口令、
段数或推理步骤。Math-Verify 负责数学表达的提取与等价判定。
只核验正式答案，不从推理中搜一个碰巧正确的数字。数值正确且由原生 EOS 结束得 1，
否则为 0。没有长度、固定思考步骤、自称反思或语言风格奖励。

报告分开展示最终数值正确率和推理／答案结构完整率。数值奖励不等于推理过程已被证实正确，
完整推理原文始终保存，供阅读和后续分析。
正确交付率以固定输出容量为条件，截断计为未交付；它不等同于开放输出预算下的数学能力。

预检先用全部标准数值检查成熟解析器可用性。不可判定的标准答案会阻止启动，
不能被偷偷跳过或转为错误样本。Windows 使用 TRL 已支持的非主线程路径，避免
Math-Verify 内部超时进程的句柄错误；已通过真实依赖的正确／错误／空答案／分数等价对照。

## 训练合同与资源限制

- `epochs` 只能大于 0 且不超过 1；`num_iterations` 固定为 1，不重复优化同一批 rollout。
- 默认每题生成 4 个回答，生成批量 4、微批量 1、累积 4，TRL 管理采样和更新。
  一次题目遍历仍会生成约 4 倍回答，不能把一个 epoch 等同于一次回答。
- 使用 NF4 QLoRA，仅更新语言层 LoRA。默认秩 8、学习率 `5e-6`、`beta=0`，
  不加载参考模型、不做额外 KL 前向。采用显式 `loss_type="grpo"`，不是依赖版本默认值。
- 原模型与训练后适配器使用相同题目、采样参数、每题种子、量化和输出容量。
  采样型单次评测存在噪声，不能只凭小幅正确率变化声称可靠提升。
- TRL 的 `mask_truncated_completions=True` 屏蔽截断回答的训练损失；报告仍记录截断。
  大量截断或全组同分意味着有效训练信号不足，不能因为优化器步数增加就声称学会了。
- 输出上限是实验资源容量，不是用户的篇幅偏好。预检发现容量不足，应在训练前另建固定配置
  的实验；不得事后只给某个分支加容量刷通过率。

单卡路径使用 Transformers 原生生成，不要求 vLLM、Ray、Liger 或自编 CUDA 扩展。
Qwen3.5 在缺少 DeltaNet 优化内核的 Windows 环境使用官方 PyTorch 后备路径，会更慢。
容量是否足够，以实际峰值显存和失败收据为准。

## 产物与恢复

需要增加已启动任务的输出容量时，保留旧目录，指定新输出目录与 `--resume-from`：

```powershell
.venv-training/Scripts/python.exe scripts/training/validate_grpo.py --model path/to/qwen35-model --output .dataforge/grpo-larger-capacity --task-source gsm8k --train-size 8 --evaluation-size 8 --max-completion-length 32768 --resume-from .dataforge/grpo-previous --action run
```

这条入口只允许容量增加及输出位置变化，模型、题目、种子、采样、优化参数与总 epoch
必须一致。先认证旧实验、原生 Trainer 检查点、优化器／RNG 文件与观察到的进度，
再使用 `Trainer.train(resume_from_checkpoint=...)` 接着跑；不是从头训练另一轮。
例如父任务已到第 1／8 步、0.125ep，子任务只完成剩余 7 步，最终累计 1ep。
已完成全部 epoch 的父任务不能通过此入口再次训练。

旧基线中已经 EOS 结束的输出可认证后复用，包括数值错误的完整回答；不按质量挑选。
旧截断输出不能作为更大容量的基线，必须重新生成。原始请求容量、完整推理与来源摘要
始终保留，不把旧请求的 4K 字段伪改成 32K。父训练记录、已提交更新与未提交采样也分开保留。

`experiment.json` 固定任务、模型文件摘要、模板、依赖与配置。
`baseline/`、`grpo_eval/` 保存逐题完整消息、实际模板、token IDs、原生推理、答案、EOS、耗时；
`grpo_train/rollouts.jsonl` 保存训练采样的同等证据和数值奖励。
`training-receipt.json` 记录 epoch、更新步数、rollout 数量、同分题组、适配器变化及峰值显存。
`report.html` 提供训练前后逐题对照与汇总。

每个优化器步由 TRL 保存检查点；恢复核验任务、模型、配置与适配器摘要。
已完成阶段认证后复用。发现观察到的更新比检查点新，或中断后无法证明尚未更新时停止，
不从头重复训练冒充一轮。失败尝试和原始采样记录保留。
训练和测评均只使用本机模型，不调用付费模型 API。

维护入口：`training/api.py` → `RunGrpoValidation` → 文件适配器与 `TrlGrpoBackend`；
组合根为 `lib/bootstrap/training_validation.py`。现有 SFT、任务、缓存与导出合同保持不变。
验证只覆盖 GRPO 领域／执行／报告、受影响训练路径和 DDD 边界。

参考：[TRL GRPO 文档](https://huggingface.co/docs/trl/en/grpo_trainer)、
[固定版本源码](https://github.com/huggingface/trl/tree/v1.18.0/trl/trainer)、
[Qwen3.5 官方模型卡](https://huggingface.co/Qwen/Qwen3.5-4B)、
[GSM8K 原始数据](https://huggingface.co/datasets/openai/gsm8k)。
