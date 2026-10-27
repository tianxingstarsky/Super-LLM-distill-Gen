# 文档适配器

`knowledge_transport.py` 使用 Qdrant 官方客户端和 OpenAI embedding SDK；保留 4 MiB 传输上限、关闭重定向、命名向量及维度校验。原知识库驱动保留连接存储与材料投影，不再手写远端协议。

`native_reader.py` 负责本地 TXT、Markdown、TeX、PDF、DOCX 读取，按格式延迟加载依赖；`corpus_writer.py` 保留 CLI 追加写入 JSONL 的行为。

[docling](docling/README.md) 隔离可选解析 SDK、模型文件、公式识别、阅读顺序、表格和图片子项。不要把 Docling 对象传成应用层的文档合同。

工作流的最终交付另有清单核验，不能把这里的 CLI 追加写入当作工作流发布。解析依赖和权重必须显式配置，导入模块不得自动下载模型。
