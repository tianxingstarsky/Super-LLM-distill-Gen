"""LoadDataFromJSONL：从 JSONL 文件加载数据（distilabel 标准自定义 GeneratorStep 模式）。

用途：管线输入统一走 JSONL 文件（可序列化进 YAML，CLI 可直接运行），
      替代 LoadDataFromDicts（其 data 字段不参与序列化，无法 CLI 直跑）。
      即 M1 导入器（lib/adapters）的雏形。

上游参照：distilabel 自定义步骤官方文档
  https://distilabel.argilla.io/latest/sections/how_to_guides/basic/step/
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List

from pydantic import Field
from typing_extensions import override

from distilabel.steps.base import GeneratorStep

if TYPE_CHECKING:
    from distilabel.typing import GeneratorStepOutput


class LoadDataFromJSONL(GeneratorStep):
    """按行读取 JSONL 文件，每行一个 dict，按 batch_size 分批产出。

    属性:
        file_path: JSONL 文件路径（相对路径以运行目录为基准）。
        fields: 可选，仅保留这些列；缺省保留全部列。
    """

    file_path: str = Field(default="", description="JSONL 文件路径")
    fields: List[str] = Field(default_factory=list, description="仅保留的列，空=全部")

    @property
    def outputs(self) -> List[str]:
        # 校验阶段即读取首行以确定列名；文件不存在时返回空（推迟到运行时再报错）
        try:
            with open(self.file_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    record = json.loads(line)
                    return [k for k in record.keys() if not self.fields or k in self.fields]
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return []

    @override
    def process(self, offset: int = 0) -> "GeneratorStepOutput":
        path = Path(self.file_path)
        records: List[Dict[str, Any]] = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                if self.fields:
                    record = {k: v for k, v in record.items() if k in self.fields}
                records.append(record)

        if offset:
            records = records[offset:]

        while records:
            batch = records[: self.batch_size]
            records = records[self.batch_size :]
            yield batch, len(records) == 0
