# 架构维护记录

当前设计见上级 [architecture.md](../architecture.md)。本目录保存 [命名约定](naming.md)、[迁移边界](migration.md) 和独立验证记录。

`implementation-notes.md` 是保留的历史实现资料，不是新增代码的默认设计。验证报告应注明代码范围、环境、失败修复、跳过项和实际测试结果。

执行流程迁移见 [多轮、生产轮次与自动评审验证](pipeline-verification-2026-10-23.md)。其中区分真实历史文件核验、冻结实现对比、离线端口测试和完整回归，不把内存替身测试写成实际百万数据生成结果。
