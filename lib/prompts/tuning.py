"""调参助手提示词：用户把当前参数与症状交给 LLM，得到带"参数意义+改动效果"的调整建议。"""
from __future__ import annotations

from lib.prompts.base import PromptSpec

TUNING_ADVISOR = PromptSpec(
    id="tuning.advisor",
    version="1.0.0",
    purpose="参数调优顾问：解释每个参数的意义、调大/调小的效果，并基于症状给出具体建议",
    source="本项目参数体系（configs/pipelines/*.yaml 与 docs/tuning-guide.md 的交互入口）",
    variables=("params_yaml", "symptoms"),
    constraints=(
        "输出必须是合法 JSON，键：explanations/adjustments",
        "explanations 必须逐条解释 params_yaml 中每个参数的意义与改动效果（调大/调小）",
        "adjustments 必须针对 symptoms 给出具体数值建议，并说明预期收益与风险",
        "不得建议改代码；只建议改配置参数",
    ),
    template="""你是 LLM 数据生成管线的参数调优顾问。用户会给你当前参数配置与遇到的问题症状。

# 当前参数配置:
{params_yaml}

# 遇到的问题症状:
{symptoms}

参考知识（参数意义速查）:
- temperature：采样随机性。调大→输出多样但稳定性下降；调小→稳定但易重复。生成类 0.8-1.0，判定类 0.2-0.3。
- thinking：思考模式。开启→推理质量高但耗 token、严格 JSON 易出错；关闭→直接输出。JSON 任务必须关闭。
- max_tokens：输出长度上限。null=不限（思考可无限长，用户已确认）。
- retries：失败重试次数。调大→更稳但更贵更慢。
- chunk_size：文档分块大小。调大→上下文完整但单块成本高；调小→问答更聚焦但知识割裂。
- faithful_threshold：回译忠实度保留线。调高→语料质量高但保留少；调低→保留多但质量下降。
- qa_per_chunk：每块问答数。调大→样本多但同质性上升。
- 质量阈值类参数：调高=更严（保留少质量高），调低=更松（保留多质量低）。

输出 JSON（不要输出其他内容）:
{{
  "explanations": [
    {{"param": "参数名", "meaning": "参数意义", "up_effect": "调大效果", "down_effect": "调小效果"}}
  ],
  "adjustments": [
    {{"param": "参数名", "current": "当前值", "suggested": "建议值", "reason": "针对症状的理由与预期收益", "risk": "潜在风险"}}
  ]
}}""",
)
