"""Use the same frozen positive authoring requirements for scoring and repair."""
from .review_style import current_claim_review_requirements
from ..domain.preference_candidates import response_projection


def preference_repair_requirements(runtime, row):
    projection = response_projection(row, row["chosen"],
        origin=(row.get("preference_response_origins") or {}).get("chosen"))
    requirements = current_claim_review_requirements(runtime, "sft", projection)
    return {"chosen": requirements, "rejected": {
        "instruction": "保留真实失败稿的原文和推理；合成负例才应用所选偏差和短推理配置。正例的互译与深度要求不自动套给负例。"}}
