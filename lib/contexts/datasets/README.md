# 数据集

负责训练记录投影、版本预览、分布统计、完整性核验、格式转换和交付。生成过程的临时输出不等同于已经发布的数据集。

入口是 [api.py](api.py) 的 `DatasetApplication` 与 `AssetCatalogApplication`；纯投影、资产和交付合同经 `contracts.py` 共享。组合根为 `lib/bootstrap/datasets.py` 与 `lib/bootstrap/asset_catalog.py`。

查看 [application](application/README.md)、[domain](domain/README.md)、[infrastructure](infrastructure/README.md)。数据读取路径必须在没有生成引擎和模型 SDK 时仍可使用。

验证：`tests/test_sample_statistics.py`、`tests/test_package_contents.py`、`tests/test_verified_preview.py`、`tests/test_asset_catalog.py`、`ui_tests/test_asset_catalog_page.py` 以及架构隔离测试。
