# 语料准备用例

`PrepareDocumentCorpus.execute` 接收 `DocumentSource`，通过 `DocumentTextPort` 取得文本，再使用领域规则规范化、分块和去重。`known_chunk_ids` 可由调用方跨文档复用。

用例不打开文件、不导入 PDF 或 Word SDK、不启动解析进程。替换读取器后，准备流程可以直接处理内存资料。

结果保持原有 `entries` 与 `stats` 合同。`duplicate_chunks` 表示实际被过滤的块，不是模型评审不合格数。空文本与不支持的 TeX 重叠方式保持原有语义。
