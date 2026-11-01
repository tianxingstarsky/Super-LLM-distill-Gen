# 数据集存储与交付

`qwen35_training.py` 使用调用方提供的 Hugging Face fast tokenizer 和实际 Qwen3.5 模板，
把平台五档映射为目标的 `enable_thinking` 开关，默认全开；用户可提供0/1映射表。
不追加五档 system 指令。`bootstrap/datasets.py` 的
`qwen35_training_encoder` 装配此适配器；它不加载权重、调用模型或写出另一份数据集。
开启时逐助手轮完整监督推理和答案；显式关闭时只在目标视图中训练答案，原记录不变。
历史上下文 label 为 -100；过长样本报错，不静默截断。
首版支持文本 SFT／多轮，工具、媒体和不支持的角色明确拒绝，不删字段冒充兼容。
模板、tokenizer 和控制映射摘要随读取视图返回；训练与推理使用同一映射。
当前监督轮推理中的 `[[SJ_REASONING_EFFORT]]` 由此适配器直接替换为目标文字，
默认“思考已开启”；构造参数 `reasoning_reference_text` 可自定义。只改内存副本，
不修改原记录或自由文本控制自述；任务引用同名标记时拒绝歧义转换。无需外部工具或 Agent。
验证：`tests/test_training_control_loader.py` 和文档中的本机 tokenizer 验证。

`driver.py` 组合文件操作；`archive.py` 打包；`statistics.py` 扫描原生记录与文件字节；`verified_preview.py` 管理有限期预览核验；`release_catalog.py` 查找已发布版本。

`corpus_index.py` 提供磁盘近重复索引与簇计数。重复判定规则仍属于纯领域算法，不能在 SQLite 实现中偷偷改变相似度或数字保护。

哈希和运行目录语义复用 `lib/infrastructure/workflow_store.py`。预览租约不能替代下载和发布时的完整性检查。统计不得把 TRL 等格式副本再次计为生成样本。

验证时包含大规模磁盘记录与内存上界，避免将批次装入列表再处理。

`asset_catalog.py` 按领域分类遍历本机文件。资料目录不进入检查点、流式日志、阶段及生产中间目录，避免它们耗尽交付文件的列举额度；高级目录仍可检查运行记录。计数只针对本次列举范围，达到上限需明确提示。文件预览、用于生成和下载继续重新核验路径与版本，不修改原文件。
