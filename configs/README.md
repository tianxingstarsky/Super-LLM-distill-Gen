# 配置与模板

保存模型连接模板、生成偏好、训练风格和命令管线配置。`recipes/` 为配方模板，`pipelines/` 为 CLI 管线定义。

本地密钥配置与公共模板必须区分；不要把 `backends.local.yaml` 等本地秘密加入版本控制。修改默认值不能重写已创建任务的配方快照。

字段含义与校验属于领域规则，读取和保存属于适配器。新字段需要明确默认值、历史配置兼容和用户在哪个节点操作。

`guided-tutor-scope.example.jsonl` 是可直接交给 `lib.guided_answers --input` 的两道公开对照题。题设相同，分别询问已测子集及整个库存，金标和演算只供私下核验。教师经验对照与封存题的运行方法见[功能说明](../docs/architecture/agent-features.md#教师经验的同题对照)。
