"""Training objectives and export contracts, independent of UI and storage."""
from __future__ import annotations

from lib.domain.workflow_quality import accepted, same_answer, text_issue, verdict

TARGETS = ("cpt", "sft", "dpo", "rlaif", "gsm8k", "cot", "orpo", "agent", "multiturn")
PREFERENCE_TARGETS = frozenset({"dpo", "rlaif", "orpo"})
INPUT_EXTENSIONS = frozenset({".md", ".txt", ".pdf", ".docx", ".json", ".jsonl"})
STAGES = {
    "ingest": "解析与来源检查",
    "cpt": "CPT 语料整理",
    "sft": "SFT 生成与验证",
    "multiturn": "多轮对话生成与一致性验证",
    "agent": "Agent 轨迹重放与剪枝",
    "preference": "DPO / RLAIF / ORPO 偏好打分",
    "gsm8k": "GSM8K 算术核验",
    "cot": "CoT 推理核对",
    "package": "质量汇总与打包",
}

TRAINING_FIELDS = {
    "cpt": ("text",),
    "sft": ("messages",),
    "multiturn": ("messages",),
    "agent": ("messages",),
    "dpo": ("prompt", "chosen", "rejected"),
    "orpo": ("prompt", "chosen", "rejected"),
    "gsm8k": ("question", "answer"),
    "cot": ("question", "reasoning", "answer"),
    "rlaif": ("prompt", "responses", "criterion"),
}


def rlaif_feedback_issue(pair: dict) -> str | None:
    """Check that the AI label and its saved evidence describe one preference."""
    if not isinstance(pair, dict):
        return "rlaif_feedback_evidence_missing"
    preference, feedback = pair.get("preference"), pair.get("rlaif")
    if not isinstance(preference, dict) or not isinstance(feedback, dict):
        return "rlaif_feedback_evidence_missing"
    if preference.get("dimension") != "correctness" or feedback.get("criterion") != "correctness":
        return "rlaif_criterion_mismatch"
    if feedback.get("judge") != "jev":
        return "rlaif_feedback_origin_missing"
    checks = (preference.get("chosen"), preference.get("rejected"))
    for name, check in zip(("chosen", "rejected"), checks):
        if not isinstance(check, dict) or not isinstance(check.get("scores"), dict):
            return "rlaif_feedback_evidence_missing"
        try:
            verdict(check)
        except ValueError:
            return "rlaif_feedback_evidence_missing"
        score = check.get("correctness")
        if type(score) is not int or not 1 <= score <= 5 or check["scores"].get("correctness") != score:
            return "rlaif_correctness_score_mismatch"
        reason = check.get("reason")
        if text_issue(reason) or feedback.get(f"{name}_feedback") != reason:
            return "rlaif_feedback_reason_mismatch"
    if not accepted(checks[0]):
        return "rlaif_chosen_not_accepted"
    if (type(preference.get("minimum_gap")) is not int or preference["minimum_gap"] < 2 or
            checks[0]["correctness"] - checks[1]["correctness"] < preference["minimum_gap"]):
        return "rlaif_preference_gap_mismatch"
    chosen, rejected = pair.get("chosen"), pair.get("rejected")
    if (not isinstance(chosen, list) or not isinstance(rejected, list) or
            not chosen or not rejected or not isinstance(chosen[-1], dict) or
            not isinstance(rejected[-1], dict) or
            not isinstance(chosen[-1].get("content"), str) or
            not isinstance(rejected[-1].get("content"), str) or
            same_answer(chosen[-1]["content"], rejected[-1]["content"])):
        return "rlaif_distinct_responses_required"
    return None


def rlaif_record(pair: dict) -> dict:
    """Render scored AI feedback; this is not an RL rollout or reward."""
    issue = rlaif_feedback_issue(pair)
    if issue:
        raise ValueError(issue)
    preference = pair["preference"]
    return {
        "prompt": pair["prompt"],
        "responses": [
            {"response": pair["chosen"], "preference_rank": 1,
             "score": preference["chosen"]["correctness"],
             "feedback": pair["rlaif"]["chosen_feedback"],
             "dimensions": preference["chosen"]["scores"]},
            {"response": pair["rejected"], "preference_rank": 2,
             "score": preference["rejected"]["correctness"],
             "feedback": pair["rlaif"]["rejected_feedback"],
             "dimensions": preference["rejected"]["scores"]},
        ],
        "criterion": pair["rlaif"]["criterion"],
    }


def rlaif_reward_model_record(pair: dict) -> dict:
    """Project checked AI labels to the explicit preference format used by TRL."""
    issue = rlaif_feedback_issue(pair)
    if issue:
        raise ValueError(issue)
    result = {key: pair[key] for key in ("prompt", "chosen", "rejected")}
    if pair.get("tools"):
        result["tools"] = pair["tools"]
    return result


def training_record(target: str, row: dict) -> dict:
    """Project a quality record into exactly the selected trainer's schema."""
    if target not in TRAINING_FIELDS:
        raise ValueError("unknown_training_target")
    if target == "rlaif":
        payload = rlaif_record(row)
    else:
        payload = {key: row[key] for key in TRAINING_FIELDS[target]}
    if row.get("tools"):
        payload["tools"] = row["tools"]
    return payload
