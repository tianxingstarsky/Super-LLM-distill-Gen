"""RepeatGenerator：按模板重复生成 n 行输入（无 LLM 字段的薄生成器）。

用途：为后续内置任务步骤（如 TextGeneration）提供固定输入行。
Magpie 无种子指令生成的 API 适配版 = 本步骤（产生空 instruction 行）
  + 内置 TextGeneration(system_prompt=Magpie 官方 system 模板)。
刻意不持有 LLM：distilabel 1.5.3 在 Windows 下自定义步骤的 llm 字段
存在 _OverlappedFuture 不可 pickle 的缺陷（内置任务无此问题，见 spike 报告）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List

from pydantic import Field
from typing_extensions import override

from distilabel.steps.base import GeneratorStep

if TYPE_CHECKING:
    from distilabel.typing import GeneratorStepOutput


class RepeatGenerator(GeneratorStep):
    """把模板 dict 重复 n_rows 次作为输入行。

    属性:
        n_rows: 生成行数。
        template: 每行的字段模板（dict）。
    """

    n_rows: int = Field(default=10, description="生成行数")
    template: Dict[str, Any] = Field(default_factory=lambda: {"instruction": ""}, description="行模板")

    @property
    def outputs(self) -> List[str]:
        return list(self.template.keys())

    @override
    def process(self, offset: int = 0) -> "GeneratorStepOutput":
        remaining = self.n_rows
        if offset:
            remaining = max(0, self.n_rows - offset)
        while remaining > 0:
            batch_size = min(self.batch_size, remaining)
            batch = [dict(self.template) for _ in range(batch_size)]
            remaining -= batch_size
            yield batch, remaining == 0
