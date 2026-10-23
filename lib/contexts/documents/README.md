# 文档处理

负责保留文档内容和结构，输出语料记录与来源证据。它不决定 SFT 的问答方式，也不负责数据集评分。

入口为 [api.py](api.py) 的 `PrepareDocumentCorpus`；纯分块与 TeX 合同经 `contracts.py` 共享。旧 `lib/doc2corpus.py` 仍可用。

- [domain](domain/README.md)：文本规范化、结构分块、TeX 保留与语料结果。
- [application](application/README.md)：协调文本读取、分块与跨文档去重。
- [infrastructure](infrastructure/README.md)：本地格式读取、文件输出及可选 Docling 实现。

资料库缓存、工作流来源快照和多模态服务调用尚未全部迁入本域。旧路径继续转发到同一份解析实现，缓存版本与解析策略标识保持原样。
