# 配置与模板

保存模型连接模板、生成偏好、训练风格和命令管线配置。`recipes/` 为配方模板，`pipelines/` 为 CLI 管线定义。

本地密钥配置与公共模板必须区分；不要把 `backends.local.yaml` 等本地秘密加入版本控制。修改默认值不能重写已创建任务的配方快照。

字段含义与校验属于领域规则，读取和保存属于适配器。新字段需要明确默认值、历史配置兼容和用户在哪个节点操作。

`guided-tutor-scope.example.jsonl` 是可直接交给 `lib.guided_answers --input` 的两道公开对照题。题设相同，分别询问已测子集及整个库存，金标和演算只供私下核验。教师经验对照与封存题的运行方法见[功能说明](../docs/architecture/agent-features.md#教师经验的同题对照)。

### 已核验算术步骤

本机师生推理服务就绪后，可用下面的命令运行丝带题。学生首答通过时会直接收为独立样本。

```powershell
python -m lib.guided_answers --protocol-version 4.10.0 --input configs/guided-calculation-plan.example.jsonl --verified-step-receipts configs/guided-calculation-plan.receipts.json --output .dataforge/ribbon-tutoring --student-url $env:STUDENT_URL --student-model $env:STUDENT_MODEL --coach-url $env:COACH_URL --coach-model $env:COACH_MODEL
```

这份公开示例的签发依据可以独立复算。将原有丝带按米编号为 1 到 18，剪下的三条分别覆盖 1–2、3–4、5–6，共 6 米，剩余 7–18，共 12 米。因此源算式中的减数表示全部已剪长度，两个数量的单位均为米。登记的收据绑定这道题、来源、标准解及两步的含义和依赖。自行引入新数据时，核验者需先检查题义、对象、单位、前置条件、是否泄漏后续结论及完整答案，再用 `calculation_plan_digest(source, steps)` 计算摘要并交给操作者加入信任清单。

步骤按依赖顺序排列。`question_span` 是原题中的依据，`equation_span` 是来源解中一条完整 `<<算式=数值>>` 注解的内部字符区间，均从零开始、右端不含。`inputs` 按算式叶子顺序绑定原题数值的位置、引用的上游 `step_id`，或明确的常量 `days_per_week`、`items_per_dozen`。例如原题的 “one-quarter” 可直接绑定四分之一，显式加 `"interpretation":"denominator"` 则取分母 4。每步还要标注所求数量 `quantity` 和单位 `unit`。最后一步代表题目所求，所有已登记步骤都必须参与它。发布时学生可看到本步算式、量、单位及题面的逐字依据，预期结果和后续步骤留在核验侧。

当前支持精确数值的四则运算及其括号组合。中文题面可定位阿拉伯数字，额外支持的文字数词限于一组英文常用数词与分数。题义所需的单位换算和量的对应关系要由核验者明确表示。涉及不等式、近似估计或开放证明的题继续使用常规披露流程。

### 从原文引文批量准备计划

已有求解器可以调用 `lib.contexts.workflows.api.prepare_calculation_plan_candidate(source, annotations)`，或按[带引文的示例](guided-calculation-author.example.jsonl)逐行导出。保留来源的原题、独立数值键与标准解文字；对 GSM8K 一类来源，`source_reasoning` 应保留原解的 `<<…>>`。求解器提供每一步的量、单位、题面引文与运算数来源。字段 `question_quote` 接受逐字短句，或 `{"text":"50","within":"each have 50 stamps"}` 这样的局部定位。上游引用前面成果用 `{"step_id":"cut"}`。步骤与标注一一对应时按原顺序取式；要使用特定注解可显式给 `equation`，其内容必须对应原文中的完整算式。

```powershell
python -m lib.guided_plan_candidates --input configs/guided-calculation-author.example.jsonl --output .dataforge/ribbon-candidates
```

输出新目录中的 `candidates.jsonl` 可交给来源核验方逐题签发。`unresolved.jsonl` 按输入行号、题号记录缺失、重名、歧义或验算失败。只要有未解决的记录，命令返回 1；同批其他已成功定位的候选会保存。候选准备不会创建信任清单。运行披露时仍须通过 `--verified-step-receipts` 提供与候选逐题对应的独立签发结果。
