# Docling 本地解析适配

此目录承接原 `lib/infrastructure/docling_*.py` 的实现。旧模块是兼容转发，内部导入使用当前包路径。不要在旧转发文件里修 bug。

| 模块 | 责任 |
| --- | --- |
| `document.py`、`runtime.py` | 解析入口、签名、页面与来源证据 |
| `preflight.py`、`worker_client.py`、`worker.py` | 依赖与权重检查、隔离进程、取消、超时、离线执行 |
| `source_pipeline.py`、`reading_order.py` | 原始块保留、标题与多栏阅读顺序 |
| `formula_crops.py`、`inline_*.py`、`math_*.py` | 局部公式截图、跨行拼接、原生字符和 LaTeX 一致性 |
| `table_*.py` | 表格结构、表头、跨单元格与混合公式 |
| `picture_*.py` | 图片素材、子项、标签及结构导出 |
| `native_*.py`、`pdf_text_visibility.py` | 字体、原文可见性和排版证据 |
| `vision_assist.py`、`visual_review.py` | 用户启用后的多模态辅助与复核 |

`worker.py` 可作为脚本运行；旧脚本入口也保留。`worker_client.py` 启动同目录 worker。解析互斥锁仍在仓库 `.dataforge/local-parser.lock`，移动代码不应建立第二把锁。

修复应保留解析策略版本、来源页码、字符范围和截图证据；不确定结果继续标记待复核。只移动代码时不改变缓存签名。

验证 `tests/test_docling*.py`。部分合同需要已安装的独立现代 Docling 环境；主环境跳过这些测试不等于验证通过。真实解析验证先使用本地权重，不需要调用模型 API。
