# 文档内容规则

`text_chunks.py` 保留段落、标题、代码围栏和表格边界。`latex.py` 保留公式、宏定义和受保护环境，不编译 TeX，不加载外部文件。`formats.py` 定义本地文本读取支持的格式。

`DocumentSource` 明确区分定位信息、文件名和格式；`CorpusBuildResult` 记录语料与重复统计。领域对象不携带 PDF 页面实例、Docling 模型或数据库连接。

不要随意更改内容哈希、空白归一化和分块边界：它们影响历史检查点及去重。TeX 的代码空白可能改变含义，不能套用普通文本的空白去重身份。

验证：`tests/test_doc2corpus.py`、`tests/test_latex_document.py` 和纯内存语料用例测试。
