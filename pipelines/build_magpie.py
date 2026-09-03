"""M0 验证 2：构建 Magpie 链管线（无种子指令生成，API 适配版）。

Magpie 技巧（API 适配）= 内置 TextGeneration + 官方 system 模板 + 空 user 回合；
上游参照：components/magpie/configs/model_configs.json 的
pre_query_template_with_system_prompt（仅取其文本，特殊 token 由服务端模板处理）。

运行：
    .venv/Scripts/python.exe pipelines/build_magpie.py
生成：
    pipelines/01_magpie.yaml
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

from lib.adapters.repeat import RepeatGenerator

OUT = ROOT / "pipelines" / "01_magpie.yaml"

# Magpie 官方 system 模板文本（上游 configs/model_configs.json）
MAGPIE_SYSTEM_PROMPT = (
    "A chat between a curious user and an artificial intelligence assistant. "
    "The assistant gives helpful, detailed, and polite answers to the user's questions."
)

with Pipeline(
    name="magpie_instructions",
    description="Magpie 链：无种子指令生成（内置 TextGeneration + 官方 system 模板 + 空 user 回合）",
) as pipeline:
    gen = RepeatGenerator(
        name="magpie_seed",
        n_rows=10,
        template={"instruction": ""},
    )
    respond = TextGeneration(
        name="magpie_gen",
        system_prompt=MAGPIE_SYSTEM_PROMPT,
        llm=OpenAILLM(
            base_url="http://127.0.0.1:8765/v1",
            api_key="sk-mock",
            model="mock-model",
        ),
        output_mappings={"generation": "instruction"},
    )
    gen >> respond

if __name__ == "__main__":
    pipeline.save(OUT)
    print(f"saved: {OUT}")
