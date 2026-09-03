"""M0 验证 1：构建最小冒烟管线并导出 distilabel 1.5.3 规范 YAML。

运行：
    .venv/Scripts/python.exe pipelines/build_smoke.py
生成：
    pipelines/00_smoke.yaml  （供 `distilabel pipeline run --config` 与测试使用）
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from distilabel.models import OpenAILLM
from distilabel.pipeline import Pipeline
from distilabel.steps.tasks import TextGeneration

from lib.adapters.load_jsonl import LoadDataFromJSONL

OUT = ROOT / "pipelines" / "00_smoke.yaml"

with Pipeline(name="smoke", description="最小管线冒烟：LoadDataFromJSONL -> TextGeneration（mock OpenAI 端点）") as pipeline:
    load = LoadDataFromJSONL(
        name="load_data",
        file_path="tests/fixtures/smoke_input.jsonl",  # 相对项目根
    )
    gen = TextGeneration(
        name="generate",
        llm=OpenAILLM(
            base_url="http://127.0.0.1:8765/v1",
            api_key="sk-mock",
            model="mock-model",
        ),
        input_mappings={"instruction": "prompt"},
        output_mappings={"generation": "response"},
        use_cache=True,
    )
    load >> gen

if __name__ == "__main__":
    pipeline.save(OUT)
    print(f"saved: {OUT}")
