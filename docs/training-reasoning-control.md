# 一份数据集，按模型转换思考强度

平台统一保存 `low`、`medium`、`high`、`xhigh`、`max`，模型适配器负责把它们转换为目标实际支持的控制。生成请求发送前转换成上游原生参数；训练加载时转换成目标模型的原生开关或配置。数据集保留统一等级、原始消息、推理和答案，不需要为不同模型再保存一套大 JSONL。

转换有两个边界。生成侧映射在发送请求时生效，不能改变已冻结的历史请求。训练侧映射在读取时生效，不能改写原始推理或复制整套数据集。上游参数是实际发送值，平台等级是统一保存值，两者应分别记录。旧版 `quick/standard/deep` 解释深度不自动重命名，也不用于猜测平台等级。

本页提供训练加载入口。新节点显式采用统一控制后，生成回执中的实际控制归属会传入训练记录；原生 JSONL 保存带版本的 `training_control.by_message`，不另存目标模型副本。旧任务和没有新控制回执的记录不会猜测标签，其原有导出字段、缓存和哈希保持原样。

## 在节点直接编辑控制文字

点击 SFT、多轮或历史 CoT 节点 → **输出适配**。默认只读，点击编辑后可开关规则、
增删规则、改匹配文字、标记名和目标文字。先预览，通过校验后保存才生效；取消修改
或保存失败保持原配置。上一版配置和默认模板也先载入编辑区，确认保存后恢复，无需改 JSON。
新版“仅标记”完整保留正文，把命中范围和标记名另存为标注；只有明确选择替换才改训练读取文字。
本地预览不调用模型。每个节点的“使用说明”解释
适用条件与常见故障，Agent 可查 [操作与排错指南](agent-operations.md)。

配置随新配方冻结，原生训练记录携带所属节点与配置快照；训练读取器自动采用记录的规则，
调用方显式配置优先。适配只改变读取视图，不改原生推理、回答或旧任务；
总开关关闭只停文字适配，不关闭模型思考。未保存的编辑不会进入新任务，旧冻结配置不热改。

## 生成时：平台选择与上游参数分开

节点模型配置中的「开启思考」使用固定五档。能力尚未确认时，先使用「更新模型能力」。已有的原生配置保留原义，主动重新选择才采用新标准；不会修改冻结任务。

`models.contracts.standard_reasoning` 创建版本化配置，节点持久化的是平台 `effort` 和冻结的 `provider_mapping`。请求适配器在发送前解析映射，供应商不会收到我们的 schema 或映射表。成功响应收据另存平台选择、实际上游配置和映射摘要，不从模型正文读取这些字段。

例如上游只声明 `low/high/max`，默认产品映射为 `low→low`、`medium→high`、`high→high`、`xhigh→max`、`max→max`。这是可查看、可替换的映射约定，不代表两种强度天然等价。预算型协议使用有输出上限约束的预算映射；只有明确思考开关的协议可合并五档。未知供应商档位不按名称猜测，需要显式映射。

```python
from lib.contexts.models.contracts import standard_reasoning, resolve_provider_reasoning

selection = standard_reasoning(
    "medium",
    model_info={"reasoning_format": "deepseek", "reasoning_efforts": ["low", "high", "max"]},
)
assert selection["effort"] == "medium"          # saved platform choice
assert resolve_provider_reasoning(selection)["effort"] == "high"  # dispatched value
```

SDK 的 chat、responses、anthropic 入口复用同一个映射边界。供应商拒绝该参数时保留错误，不以去掉思考参数重试来假装适配成功。多轮按真实助手轮记录；答案或前缀经过修改时，失去来源依据的旧标签退休到历史。拼接推理若来自不同或未知档位，保留原内容及各次收据，不给整段强贴单一标签。普通重评不会改动作者档位。

自动传播覆盖已捕获可信响应详情的普通／定向 SFT、原生 CoT 和多轮路径。旧版正文风格化、分阶段作者路径若没有捕获这些详情，暂不自动写样本档位；不能用审核模型参数或最后一次调用替它补标。生成端仍执行保存的映射，训练侧对缺失标签明确报错或使用调用方指定的读取默认值。

## 标准记录

```json
{
  "training_control": {
    "schema": "shujian.training-control/v1",
    "effort": "high"
  },
  "messages": [
    {"role": "user", "content": "两组测量值不同，能说明干预有效吗？"},
    {
      "role": "assistant",
      "reasoning_content": "我需要区分观察到差异和识别因果效应。组间差异也可能来自混杂因素或抽样误差，因而还需查看分组方法、误差范围及对照条件。",
      "content": "仅凭两组测量值不同，还不能确定干预有效。需要检查分组、对照和不确定性，才能判断差异是否由干预造成。"
    }
  ]
}
```

上例只说明字段结构，不是该条样本已达到 `high` 质量的评测结论。强度是控制配置，不是质量分，也不是字数配额。具体模型可能只有两档；平台等级仍保留，通过明确映射适应该模型。

新增记录时，可显式附加控制：

```python
from lib.contexts.datasets.api import with_training_control

controlled_record = with_training_control(training_record, "high")
```

函数返回深拷贝，不改原对象。已有不一致控制会报错，不自动覆盖。`effort` 适用于整条记录中的所有助手目标轮。多轮的强度不同则使用 `by_message`，两种形式二选一：

```json
{
  "schema": "shujian.training-control/v1",
  "by_message": {"1": "low", "3": "max"}
}
```

索引是源 `messages` 中从零开始的位置，键以字符串保存；这里只能指向助手消息。上例用于 `user → assistant → user → assistant` 对话，不改变消息排列。加载器按每个目标轮的等级映射，不能把一次节点设置误套到所有历史助手轮。

没有控制字段的旧记录或 `by_message` 中缺失的助手轮默认报错。明确选择本次加载条件时，可以传入 `default_effort="high"`。它只补充缺失部分的读取视图，不修复坏格式，不覆盖已有值，不从教师参数或推理长度推断标签，也不证明旧生成实际使用了这个档位。缺失部分的来源应记录为显式默认值，不能当作源记录自带标签。

## 第一版：Qwen3.5 文本适配

文中的 `<QWEN35_MODEL_DIR>` 和 `<TRAINING_ENV>` 分别代表本机模型目录与独立训练环境，需替换成使用者自己的位置。

本机完整 Qwen3.5-4B 的 safetensors 权重目录已确认为 `<QWEN35_MODEL_DIR>`。另外存在 Ollama `qwen3.5:0.8b` Q8_0，早期模板检查使用过这一份；它与完整 4B 训练权重分别管理。加载示例接收用户指定的 Qwen3.5 tokenizer 目录，不固定权重大小。

项目 `.venv` 中的 Transformers 4.57 可以完成 tokenizer 预处理，但没有 Qwen3.5 模型架构实现，不能据此声称权重能在该环境中训练。本机 `<TRAINING_ENV>/Scripts/python.exe` 已有 Transformers 5.16 和带 CUDA 的 PyTorch 2.11，可作为后续训练环境。**本轮只验证加载转换，没有启动微调，也没有训练后效果结论。**

Qwen3.5 的公开模板使用 `enable_thinking` 开关。本适配器直接把平台五档映射到这个原生开关，不注入额外五档 system 提示，也不要求模型重新学习五档。官方模板和模型说明分别见 [Qwen3.5-4B 模板](https://huggingface.co/Qwen/Qwen3.5-4B/blob/main/chat_template.jinja) 与 [模型卡](https://huggingface.co/Qwen/Qwen3.5-4B)。本地验收另记录实际模板摘要，避免上游 main 更新后混用结论。

| 平台保存值 | 默认示例 | 可选 `low` 关闭示例 |
| --- | --- | --- |
| `low` | `True` / `1` | `False` / `0` |
| `medium` | `True` / `1` | `True` / `1` |
| `high` | `True` / `1` | `True` / `1` |
| `xhigh` | `True` / `1` | `True` / `1` |
| `max` | `True` / `1` | `True` / `1` |

默认全部开启是明确的适配选择：目标只有开关时，五个保存值可以映射到同一原生值。它不意味着目标会表现出五种不同强度。需要把 `low` 用作关闭思考的任务，可以改用 [low 关闭映射](../scripts/training/qwen35-low-off.json)；默认映射见 [全部开启](../scripts/training/qwen35-thinking-on.json)。以后适配支持多档的模型时，只需改变小型映射和目标适配器，不改数据集。

输入控制转换与推理正文转换是两个不同功能。本示例不会搜索替换 `verbosity 5` 等模型自述，也不会从这些词反推样本档位。两条既有原始响应已显示控制复述分布在推理中部、尾部且有措辞变化；具体位置和来源限制见 [控制复述核对](architecture/request-provenance-2026-11-01.md#控制输入与推理复述的位置核对)。

### 初始系统的固定引用与本地替换

新默认 `workflow.cot_native_joint@1.6.0` 在原生 SFT、多轮和 CoT 的首条 system 中约定：若推理需要提及本轮思考强度，只使用 `[[SJ_REASONING_EFFORT]]`。无需每条出现，不规定位置或推理结构；不用自由文本猜测控制值。用户本来在讨论模型参数的业务内容仍照常回答。原始响应完整保留，旧任务、旧模板和自定义正文不热改；已有节点可显式恢复新版默认。

现有 `Qwen35TrainingEncoder` 在训练读取时直接精确替换这个标记。默认五档都开启思考，因此替换为“思考已开启”；可以通过 `qwen35_training_encoder(..., reasoning_reference_text="Thinking is enabled")` 自定目标文字。这里只改变当前监督助手轮的内存副本，保留其余推理和答案；原记录中的平台等级及原始响应不变。`metadata.reasoning_reference_replacements` 记录本轮替换次数，目标文字也记录在适配配置中。任务正文若引用了同一标记则拒绝歧义替换，不改写用户原话或代码示例。

这是普通的程序内字符串替换，无需 DSH、Codex、MCP 服务或额外模型请求。默认没有标记时保持原文；下节提供显式启用的 DeepSeek 字面匹配配置。替换文字是训练文本的适配；真正启用 Qwen 思考的仍是 `enable_thinking` 与官方模板。最初本地接线阶段未调用 API，实际生成观察另见下段。

后续授权的两条 V4.1-Flash 实测均未产生标记，仍复述 verbosity 5，实际替换为零。
思考强度标记不能自动覆盖输出详尽程度自述；不能把这次结果宣传为标记治理成功。

### 旧版 DeepSeek 占位符转换：保留兼容

下面是旧 v1 接口的行为，不是新版“仅标记”。选择 `source_control_profile="deepseek-v4.1-flash-control/v1"` 后，程序直接将已观察到的
`Desired oververbosity 5` 和 `desired verbosity 5` 替换为 `[[SJ_OUTPUT_VERBOSITY:5]]`。
无需让模型重新输出。匹配保持大小写和词边界，不吞掉后续标点，不匹配 50、5.0、其它数值
或未确认的表达。这里只处理当前监督助手轮的推理；源文件、用户原话和答案保持不变。
若任务正文讨论这些字段，转换会报歧义错误，不改写任务引用。

```python
encoder = qwen35_training_encoder(
    tokenizer,
    source_control_profile="deepseek-v4.1-flash-control/v1",
    # Omit this argument to retain the portable marker.
    verbosity_reference_text="输出详尽程度：5",
)
```

这段目标文字只是替换示例，不是 Qwen 的原生参数。`verbosity_reference_text` 不允许空串：
此入口用来替换控制表达，不用来删掉它。平台 `low` 到 `max` 以及 Qwen 的 `enable_thinking`
仍按独立的思考强度配置工作，不能把模型自述的 verbosity 5 当作已认证的档位。
对混合教师来源的数据，应按来源选择适配器；不能给未知来源统一套 DeepSeek 配置。

每轮读取的 `metadata.source_control_edits` 保存原文、原位置（零起始 Unicode 字符下标，
末端不含）、标记与观察值；`source_reasoning_sha256` 绑定完整原生推理。
`verbosity_reference_replacements` 记录标记到目标文字的替换次数。
配置和映射摘要随读取视图保存，源数据不需要迁移，也不用再导出一套大型数据集。
这些记录描述本地转换，不改变样本质量或合格状态。

版本化固定标记能让特殊控制的训练和适配更一致；自然推理仍保持自由表达。
重复输出足以支持对已确认字面串做定向适配，但不能单凭这些输出证明上游训练时一定
由程序加入了字段，或已确认存在过拟合。新拼写先保留原文，确认后再增加适配版本。
完整事实与消耗见 [真实复跑记录](architecture/request-provenance-2026-11-01.md#16-初始提示词的两条真实复跑)。

### 修改、停用和保存文字规则

新配置使用 `shujian.control-text-adaptation/v2`，可从 `default_control_text_config()`
取得独立副本。节点界面优先用于日常编辑；下表和代码供训练集成使用。
[control-text-deepseek.json](../scripts/training/control-text-deepseek.json) 保留旧 v1 示例，
其 null 仍表示转换成占位符。不要把旧语义当成新版仅标记，或改写冻结任务来升级。

| 配置 | 效果 |
| --- | --- |
| 顶层 `enabled: false` | 完全关闭控制文字适配，保留原文和已有标记 |
| 规则的 `enabled: false` | 只停用这条规则，其它启用规则继续工作 |
| `matches` | 精确匹配的原文列表；区分大小写，不把它当正则执行 |
| `marker` | 标注中的稳定名称，用于定位和后续映射，不插入正文 |
| `replacement: null` | 仅记录命中范围和标记名，正文每个字符都保留 |
| `replacement: "目标文字"` | 同次读取时替换为该文字；空串不允许 |
| `matches: []` | 只识别正文中已存在的标记；有明确目标文字才替换 |

```python
from lib.contexts.datasets.api import default_control_text_config

config = default_control_text_config()  # v2, both defaults mark without replacing text.
config["enabled"] = False  # Turn text adaptation off, not model thinking.
encoder = qwen35_training_encoder(tokenizer, control_text_config=config)
```

命令行使用 `--control-text-config path/to/config.json`。加上 `--no-control-text` 可临时完全
关闭，优先于 JSON 和旧文字参数。JSON 的关闭状态也会禁止旧 `[[SJ_REASONING_EFFORT]]`
自动替换；真正的 `enable_thinking` 仍由独立的五档映射决定。

新配置完全管理文字适配，不能与旧 `source_control_profile`、`verbosity_reference_text`
或自定义 `reasoning_reference_text` 混用。未传新配置的历史调用保持原行为。
每个加载器冻结配置副本，并在 `metadata.control_text_configuration` 中保存快照及对应摘要；
修改外部字典或文件只影响下一次创建的加载器，不热改当前批次。

启用规则的冲突会在加载时提示。替换只扫描原输入一次，不把一条规则的输出继续交给另一条；
重复读取也会检查任务是否正在讨论该字段。v2 的 `source_control_annotations` 保存命中
范围、原文、规则 ID 和标记名；`source_control_edits` 只记录明确替换，
`control_text_replacements` 记录其次数。仅标记时后两者分别为空列表和零，正文保持原样。
原始任务、响应和导出不变。旧 v1 继续使用原有行为和回执。

适配器通过 Transformers 的 `apply_chat_template` 使用模型模板。当前接入的是文本 SFT 和多轮对话。工具、媒体、developer 角色、非开头的 system 消息不在本版支持范围内，会明确报错。连续开头的 system 内容按相同优先级合并。思考开启时目标助手缺少推理会报错，不补造；关闭时生成空 think 块，只监督答案。关闭只是本次加载视图的选择，原始记录中的完整推理不删除。

Qwen 模板通常不把较早助手轮的推理保留在最新一轮上下文中。因此加载器对每个助手轮分别创建一个监督视图：此前消息是上下文，该轮按原生开关选择推理与答案的监督范围。开启时保留该轮完整推理，不会只监督最后一轮而遗漏此前推理。每个源助手轮只作为目标一次；多个视图仍在内存中按需产生。官方聊天模板的使用方式见 [Transformers 文档](https://huggingface.co/docs/transformers/main/en/chat_templating)。

## 先检查真实输入

```powershell
.venv/Scripts/python.exe -X utf8 scripts/training/preview_qwen35.py --input path/to/sft.jsonl --tokenizer path/to/qwen35-tokenizer --take 2 --show-text
```

只使用本地 tokenizer 目录；脚本不会下载文件。输出包含模板、tokenizer、控制映射的摘要，以及每个目标的输入 token 数和受监督 token 数。`--show-text` 展示完整解码输入和目标，方便核对原生开关与监督范围是否对应、用户文字是否正确排除在答案损失之外。

使用另一份小型映射，无需再导出数据：

```powershell
.venv/Scripts/python.exe -X utf8 scripts/training/preview_qwen35.py --input path/to/sft.jsonl --tokenizer path/to/qwen35-tokenizer --effort-mapping scripts/training/qwen35-low-off.json --take 2 --show-text
```

检查时区分两种结果：开启思考的目标含原始推理和答案；关闭思考的输入使用空 think 块，监督目标为答案，源文件仍保留推理。

设置 `--max-length` 后，超过容量的样本会报错。不能用截断推理、删答案或只保留最后一轮的方式把预处理变绿。容量也不是用户偏好的篇幅限制。

## 接入自己的训练器

以下是数据入口示例，不会加载权重或启动训练。使用一个读取进程，按文件顺序完成一次遍历：

```python
from pathlib import Path
from torch.utils.data import DataLoader, IterableDataset, get_worker_info
from transformers import AutoTokenizer, DataCollatorForSeq2Seq
from lib.bootstrap.datasets import qwen35_training_encoder
from lib.contexts.datasets.api import iter_training_examples
from scripts.training.preview_qwen35 import iter_jsonl_records

tokenizer = AutoTokenizer.from_pretrained(
    "path/to/qwen35-tokenizer", local_files_only=True, trust_remote_code=False
)
encoder = qwen35_training_encoder(
    tokenizer, max_length=32768,
    effort_mapping={"low": 0, "medium": 1, "high": 1, "xhigh": 1, "max": 1},
)

class TrainingStream(IterableDataset):
    def __iter__(self):
        if get_worker_info() is not None:
            raise RuntimeError("This example requires num_workers=0")
        records = iter_jsonl_records(Path("path/to/sft.jsonl"))
        for example in iter_training_examples(records, encoder):
            # Keep provenance in logs if needed; model.forward does not receive it.
            yield {name: example[name] for name in ("input_ids", "attention_mask", "labels")}

collator = DataCollatorForSeq2Seq(
    tokenizer=tokenizer, model=None, padding=True, label_pad_token_id=-100
)
loader = DataLoader(TrainingStream(), batch_size=1, num_workers=0, collate_fn=collator)
# Consume loader once in your trainer. This example does not call train().
```

保留已有 `labels`：提示和历史上下文使用 `-100`，当前助手的目标按原生开关确定。不要改用会按 `input_ids` 重建标签的语言模型 collator。不要把 `metadata` 传给模型。训练器若还会再次应用模板、补特殊 token 或截断，应关闭相应预处理，避免重复编码。

这里的示例只覆盖单进程读取。多 worker、多机和多卡需要明确的数据分片，不能让每个进程重复遍历全文件。采用 Hugging Face Datasets 时，可以以 `load_dataset("json", data_files=..., split="train", streaming=True)` 读取，再传给同一应用用例；保持流式，不调用 `list()`、`save_to_disk()` 或写一套目标模型副本。[Datasets 流式文档](https://huggingface.co/docs/datasets/stream)

本项目当前微调实验仍遵守不超过 **1 epoch** 的约定。逐助手轮视图会增加编码工作，但不会重复监督同一目标轮。不要通过重复遍历或无限循环来凑训练步数。数据划分应按源对话或文档进行，再展开助手轮，以免同一上下文进入训练集和评测集。

## 推理时使用同一控制映射

训练后运行模型时，仍由同一适配器构造提示：

```python
prompt = encoder.render_prompt(
    [{"role": "user", "content": "两组测量值不同，能说明干预有效吗？"}],
    "high",
)
model_inputs = tokenizer(prompt, add_special_tokens=False, return_tensors="pt")
```

与训练产物一起保存 `encoder.provenance`，记录 tokenizer、模板和控制映射版本。平台值可以保持一致，由目标映射决定实际开启或关闭；部署时应明确采用哪份映射。后续模型通过实现同一 `encode_turn` 端口接入自己的原生控制语法，原始数据字段保持统一。

预处理验证能证明映射、角色、监督标签和推理边界正确，不能证明训练后的回答质量。本轮不启动微调。后续应在相同未见任务上比较实际原生配置的正确率、条件覆盖、重复和消耗；多个平台值映射到同一个开关时，不能把它们宣传成已经具有不同的目标模型行为。

## 本机验收记录（2026-11-01）

已用完整 4B 目录 `<QWEN35_MODEL_DIR>` 的实际 tokenizer 和模板，以及之前找到的
0.8B 模板完成离线转换。默认五档输入一致、均开启思考；自定义 low=0 时使用官方空
think 前缀且只监督答案。双轮中的两次助手推理分别获得完整监督，源记录不变；过长输入
明确拒绝。4B 模板摘要为
`a4aee8afcf2e0711942cf848899be66016f8d14a889ff9ede07bca099c28f715`。
模板默认值不同，因此适配器始终显式传入布尔开关。这里只加载分词器，没有加载模型权重、
启动训练或调用付费 API。转换明细保存在本机 `.dataforge/training-adapters/loader-verification.json`。

固定标记的本地适配也已通过同一 4B tokenizer 检查：逐轮推理与答案完整进入监督文本，
自定义替换文字生效，普通自由文本保持原样，任务引用歧义被拒绝，关闭思考的既有映射保持，
源记录未改动。证据在 `.dataforge/runtime-control-governance/internal-mapping-verification.json`；
没有模型调用或训练，不能据此声称新初始提示词已经获得稳定遵循率。

后续 DeepSeek 专用配置复用同两条完整响应做离线验收：各两处、共四处字面自述全部标记，
目标文字转换后完整推理与答案仍进入本机 4B tokenizer 的监督范围，原响应摘要不变。
另验证多轮逐助手轮读取和六种任务引用歧义。证据在
`.dataforge/deepseek-control-marking-20261101/training-verification.json`；本次新增 API 调用为零。
这是程序适配成功，不是新一轮生成或语义质量通过。

可编辑配置随后复用同两条原生响应，通过总关闭、默认标记、自定义文字、单条关闭四种
加载视图的本机 tokenizer 检查；总关闭同时保留已有思考强度标记。命令行关闭优先级也已
通过真实入口确认。证据在 `.dataforge/deepseek-control-config-20261101/verification.json`
和 `cli-off.txt`，新增 API 调用为零。
