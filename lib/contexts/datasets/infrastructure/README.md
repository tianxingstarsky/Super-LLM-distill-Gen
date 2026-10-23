# 数据集存储与交付

`driver.py` 组合文件操作；`archive.py` 打包；`statistics.py` 扫描原生记录与文件字节；`verified_preview.py` 管理有限期预览核验；`release_catalog.py` 查找已发布版本。

`corpus_index.py` 提供磁盘近重复索引与簇计数。重复判定规则仍属于纯领域算法，不能在 SQLite 实现中偷偷改变相似度或数字保护。

哈希和运行目录语义复用 `lib/infrastructure/workflow_store.py`。预览租约不能替代下载和发布时的完整性检查。统计不得把 TRL 等格式副本再次计为生成样本。

验证时包含大规模磁盘记录与内存上界，避免将批次装入列表再处理。
