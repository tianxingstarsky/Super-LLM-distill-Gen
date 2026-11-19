# 本地训练验证

## Qwen3.5 语言模型全参 SFT

4.10 的认证算术目标记录每步列式、数值执行方和正式读出者，能按学生提出算式或已给全部算式区分训练用途。[生成、目标归属与重放指南](guided-reasoning.md)

在配好下方独立训练环境后，从项目根目录运行。将三个参数替换为本机 Qwen3.5 模型目录、已核验的 UTF-8 `messages` JSONL 和独立训练工作目录。

```powershell
python -m lib.cli train-sft --model path/to/qwen35 --sft path/to/training-targets.jsonl --output .dataforge/native-sft
python -m lib.cli train-sft --model path/to/qwen35 --sft path/to/training-targets.jsonl --output .dataforge/native-sft --action run
```

第一条做 CPU 预检，输出源文件 SHA-256、实际助手轮数、带推理与仅答案的轮数、监督 token 数及最长输入。普通多轮对话中的每个助手轮都是一个训练目标。有独立 `reasoning_content` 的轮次监督推理、回答和结束标记。仅有答案的轮次屏蔽 Qwen 模板的空思考前缀，监督回答和结束标记。超过原生上下文或显式 `--max-length` 上限会报告源行号。预检拒绝 `unverified` 或 `quarantine` 记录。

披露式教学协议 4.9.1 的训练输入是生成目录中的 `training-targets.jsonl`。每题只有一个受监督助手轮。独立首答使用学生实际答复；独立解题使用受审学生原稿中保留的推导。修正题把旧稿、当时的正式答复和公开提示写入带来源标识的题目上下文，以修正后的推导和核验的正式答复作为目标。给过完整解的题目归入 `teacher_demonstration`，使用由题源验证收据绑定的解法，并记录源解作者（已知时）和正式答复作者。接受的推导有审核和末答核对证据，逐句语义质量仍取决于这些审核。

`summary.json` 的 `training_targets` 给出分目标数量和保留待处理的原因；训练预检的 `guided_targets_by_objective` 统计实际通过训练筛选的目标。归档 `accepted.jsonl`、各题 `case.json` 和模型请求收据保留完整旧稿、披露和交付历史。新协议归档标注训练文件，公共训练加载器会引导调用方读取投影。旧协议的导出和已冻结训练输入按原合同读取。需要重新导出 4.9.1 的投影时，使用原始题目文件、原运行目录和 `python -m lib.guided_answers --input path/to/tasks.jsonl --output path/to/run --export-only`，无需模型服务。

第二条在 CUDA 上单次遍历目标并训练全部语言参数，视觉权重随包保留。默认梯度累积 8，学习率 `8e-6`，可用 `--gradient-accumulation`、`--learning-rate`、`--seed` 指定。运行期使用输出目录内的临时编码文件。训练和断点位于 `full-training/`，完成后 `model/` 包含可独立加载的模型和核验收据。按相同模型、来源、设置和输出路径重新运行会核对身份并复用已有进度。运行中可在 `full-training/` 创建 `STOP` 文件申请在检查点暂停，删除这个标记后使用同一命令继续。

## 原模型、SFT、CPT→SFT 对照

这个入口把已导出的文本训练记录接到真实模型验证：原模型、SFT、CPT→SFT 使用同一份固定测试题，保存完整提示、原生输出、思考、答案和训练收据。它不重写生成任务、缓存或原始导出文件，也不调用付费模型 API。

这是固定小批量的下游回归入口。当前文件适配器在内存中编码实验输入，适合这里的百条级对照；不把它宣传为百万条训练器。既有百万级生成批次仍使用原流式端口，没有改成列表。

## 依赖与职责

使用 Hugging Face Transformers `Trainer`、PEFT LoRA 和 bitsandbytes NF4。数据预处理复用项目现有 Qwen3.5 编码器，Trainer 使用预先确认的 `labels`，不重新生成监督掩码。训练不是新的一套 Agent 调度器，核心数据生成依赖也不加入这些可选框架。

在独立的 Python 3.12 环境安装 `requirements-training.txt`。PyTorch 的 CUDA wheel 应按本机 GPU 和驱动从 [官方安装入口](https://pytorch.org/get-started/locally/) 选择；CPU 版本不能用于本入口。不要把训练依赖安装进桌面运行时、语音环境或用户的 Agent 环境。

```powershell
python -m venv .venv-training
.venv-training/Scripts/python.exe -m pip install -r requirements-training.txt
```

本地模型目录应包含有效的 `config.json`、tokenizer、官方模板和完整 safetensors。下载失败的占位文本不能当作配置。若修复本机不完整的元数据，应创建独立模型视图、记录原文件和修复摘要，保持原权重不变；不能把加载失败归因于训练数据质量。

当前适配范围是 Qwen3.5 文本问答：视觉模型结构可以加载，但本对照不训练图像输入。QLoRA 的 LoRA 目标只包含语言模块，不更新视觉模块或输出头。它验证的是同一量化条件下的适配器变化，不能冒充全参数训练。

## 输入与预检

准备以下文件：

| 参数 | 内容 |
| --- | --- |
| `--model` | 本地 Qwen3.5 模型目录 |
| `--sft` | 训练 JSONL，包含真实 user、assistant 与完整推理 |
| `--validation` | 与训练和测试问题隔离的验证 JSONL |
| `--cpt` | 只含论文正文的 UTF-8 文本或 Markdown，不含测试答案 |
| `--evaluation` | JSONL：`id`、`messages`、可选 `expected_answer`、`rubric`、`group` 和 `language` |
| `--output` | 忽略的本地实验目录，保存快照、适配器和报告 |

`expected_answer` 和 `rubric` 仅用于事后查看，不进入生成提示。`messages` 必须是实际要交给模型的上下文。使用固定留出问题，不能在看到输出后换题或挑选最佳样本。

```powershell
.venv-training/Scripts/python.exe -X utf8 scripts/training/validate_local.py --model path/to/Qwen3.5-4B --sft path/to/train.jsonl --validation path/to/validation.jsonl --cpt path/to/paper.md --evaluation path/to/evaluation.jsonl --output .dataforge/training-validation --action preflight
```

预检不加载模型权重、不更新参数。它用实际 tokenizer/template 检查：

- system 原文在上下文中保留，提示部分的 labels 为 `-100`。
- 完整推理、正式答案与 `<|im_end|>` 均在助手监督目标中；官方模板末尾的换行允许保留。
- 超长记录明确拒绝，不静默截短推理或答案。
- 训练、验证、测试问题没有规范化后的完全重复。这不等于证明没有语义重叠。
- CPT 分块保留全部 token，只在文档结束添加一次结束标记，不给每个中间片段伪造文档结束。

来源没有 system 时，只在实验读取视图中加入 `--system-message` 指定的中性消息，并标为 `experiment_added`，不假装原样本带有 system。没有推理时不会在训练前自动编造。已有教师解释也不自动声称是教师的原生思考。

## 单次遍历与恢复

检查 `preflight.json` 和 `supervision-audit.json` 后，使用完全相同参数把 action 改为 `run`。`df train-validate` 提供同一入口。

```powershell
.venv-training/Scripts/python.exe -X utf8 scripts/training/validate_local.py --model path/to/Qwen3.5-4B --sft path/to/train.jsonl --validation path/to/validation.jsonl --cpt path/to/paper.md --evaluation path/to/evaluation.jsonl --output .dataforge/training-validation --action run
```

`--epochs` 只接受 `1`。SFT 分支遍历一次 SFT；CPT→SFT 分支由同一 Trainer 按“CPT 块 → 相同 SFT 顺序”遍历联合数据一次。两组独立从同一原模型开始，不把 SFT 适配器当成 CPT 分支起点，不在中间重新洗牌或补跑第二轮。不同训练样本的长度不同，报告同时记录实际样本数、监督 token、优化步和 epoch，不能只比步数。

这里的“先后”指样本课程顺序。若 CPT 块数不是梯度累积数的整数倍，边界上的同一次优化更新可能同时累积最后的 CPT 块和最前面的 SFT 样本；这不是两个独立 Trainer 阶段。报告保留块数和累积数，不把它描述成严格分开的两段优化实验。token 计数指非 `-100` 的 labels；因果模型执行一位 shift，CPT 每块的第一个 token 只提供上下文，不预测自己。

验证集在预检中检查完整性和题目隔离，并对三组分别计算助手监督 token 的验证 loss。纯量化原模型不能通过当前 Trainer 的训练保护检查，因此三组统一直接使用官方模型的只读前向 loss，按 shift 后有效目标 token 数加权；不伪装成带适配器的模型，也不改监督掩码。本次固定一轮不使用它挑选轮次。测试集只用于冻结模型后的生成对照。不要把这称为验证集早停，loss 也不能代替开放生成正确率。

实验清单锁定输入、权重、模板、版本和配置。再次运行时只接受同一身份；已提交并核验的阶段直接复用。训练默认每个优化步保存检查点，保留两个；从完整的 Trainer 检查点恢复剩余部分，不额外训练一轮。如果已观察到的训练步数比检查点更新，或硬中断后无法证明尚未训练，停止并保留现场，不从旧位置偷偷重训。评测按题目落盘，恢复时跳过身份一致的完整记录；中途未完成的单题可以从相同 seed 重新生成，这与重复训练不同。若输入或配置需要改变，使用新的输出目录，不覆盖原对照。

采样固定为 thinking 模式，`temperature=1.0`、`top_p=0.95`、`top_k=20`、`min_p=0`、`repetition_penalty=1.0`。官方还推荐 presence penalty，但本次 Transformers 5.23 不原生支持，收据会明确记录未应用，不传一个实际上无效的参数，也不声称完整复现了另一推理框架的采样行为。默认输出容量为 32,768 tokens；它是截断保护上限，不是输出长度目标。实际推理与答案都保留，达到上限明确单列。

Windows 可以使用这里的 CUDA 路径。本次环境未安装可选的 `causal_conv1d` 与 `flash-linear-attention` 高速内核，Transformers 会使用其 PyTorch 参考实现；警告本身不代表解析或训练失败，但不能把该环境的耗时当作经过吞吐优化的生产性能。

## 阅读结果

`report.html` 展示三组完整输出；`experiment.json` 保存固定实验信息；`supervision-audit.json` 展示真实模板和监督文本；各阶段保留 Trainer 状态、适配器摘要和逐题生成记录。适配器、模型和原始输入只在本地忽略目录保存，不进入公开源码。

终止正确、存在正式答案、思考闭合和答案正确是不同指标。达到输出上限、只有思考没有答案或终止异常必须单列，不能算作通过。格式完成率、训练 loss 和教师自评分都不是事实正确率。语义判断必须有逐题标准和实际输出证据，注明谁做的判断，不能把助手离线审阅冒充经过校准的人类基准。

程序默认不购买评审，也不自动宣称语义通过。人工或外部评测可另存 `semantic-assessment.json`：顶层使用本实验的 `experiment_identity`，`cases` 每项提供 `case_id`、相对的 `artifact`、实际文件 `output_sha256`、0–2 的 `semantic_score`、`reasoning_material_contradiction`、`combined_usable`、原文证据和说明。`reasoning_material_contradiction=null` 表示未定。报告核验输出身份、摘要和评分字段；综合可用必须同时满足答案2分、未发现实质推理矛盾、非空推理和答案、思考闭合、EOS及未截断，不能靠外部直接写 `true` 放行。未知结果保持待复核。评分解释应在生成前固定，程序不代替用户建立金标准。本次真实结果见 [Qwen3.5-4B 单轮对照](local-training-validation-2026-11-16.md)。

同论文留出题只能测试这一小批任务的迁移。原始 Qwen3.5-4B 是已后训练模型，可能本来就知道经典论文；这里的“原模型”并非另外下载的 `Qwen3.5-4B-Base`。小数据、单随机种子的结果不能证明通用知识提升、规模收益或生产质量。

实现入口：`lib/contexts/training/api.py`；组合根：`lib/bootstrap/training_validation.py`。验证运行 `tests/test_training_validation.py`、`tests/test_training_validation_backend.py` 和 `tests/test_ddd_architecture.py`，不因此启动全仓回归。

参考：[官方模型说明](https://huggingface.co/Qwen/Qwen3.5-4B)、[PEFT 量化训练](https://huggingface.co/docs/peft/en/developer_guides/quantization)、[Transformers Trainer](https://huggingface.co/docs/transformers/en/main_classes/trainer)。
