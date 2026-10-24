"""Validate configured negative designs using anonymous response grades."""
from __future__ import annotations

from copy import deepcopy
from lib.contexts.workflows.contracts import preference_score_issue
from .candidate_content import candidate_digest, _payload_issue, _row_training_surface_issue
from .preference_feedback import preference_release_feedback
from lib.domain.turn_review import reasoning_text
from lib.contexts.workflows.contracts import validate_preference_design


def response_projection(record, response, *, origin=None):
    """A reviewer sees one actual conversation, with no pair label or sibling."""
    fields = ("id", "source_id", "source_context", "qa_contract", "tools", "evidence_level")
    projected = {name: deepcopy(record[name]) for name in fields if name in record}
    projected["messages"] = [*deepcopy(record["prompt"]), *deepcopy(response)]
    if origin is not None:
        projected["reasoning_origin"] = origin
    projected["_complete_preference_response"] = True
    return projected


def response_surface_issue(projected, recipe):
    return _payload_issue("sft", projected) or _row_training_surface_issue("sft", projected, recipe)


def unscored_preference(record):
    row = deepcopy(record)
    for field in ("preference", "rlaif", "preference_assessment", "learner_context_check"):
        row.pop(field, None)
    return row


def rejected_positive(record, assessment, *, design):
    row = unscored_preference(record)
    pending = bool(assessment.get("condition_pending"))
    stop = "condition_support_pending_review" if pending else None
    row["preference_assessment"] = {"version": 1, "policy": "explicit_contrast_with_anonymous_quality_scores",
        "responses": [deepcopy(assessment)], "design": deepcopy(design), "stop_reason": stop,
        "negative_assessment": "not_performed_positive_not_accepted", "model_judgments_not_independent_truth": True}
    context = assessment.get("learner_context_check")
    if context is not None:
        row["learner_context_check"] = deepcopy(context)
    check = deepcopy(assessment["verdict"])
    check["keep"] = False
    if pending:
        check["reason"] += "; condition_support_pending_review"
    elif isinstance(context, dict) and context.get("keep") is False:
        check["reason"] += "; learner_context_rejected: " + str(context.get("reason", ""))
    return row, check, stop


def assessed_preference(record, assessments):
    """Never swap declared roles or alter grades to satisfy the design."""
    if len(assessments) != 2:
        raise ValueError("preference_candidate_assessment_count")
    responses = [deepcopy(record["chosen"]), deepcopy(record["rejected"])]
    hashes = [candidate_digest(response) for response in responses]
    if any(item.get("response_sha256") != digest for item, digest in zip(assessments, hashes)):
        raise ValueError("preference_candidate_assessment_mismatch")
    if any(item.get("structural_issue") for item in assessments):
        return None, "preference_candidate_surface_invalid"
    high, low = 0, 1
    row = deepcopy(record)
    row.update(chosen=responses[high], rejected=responses[low])
    row["preference"] = {"dimension": "correctness", "minimum_gap": 2,
        "chosen": deepcopy(assessments[high]["verdict"]), "rejected": deepcopy(assessments[low]["verdict"])}
    row.pop("rlaif", None)
    row["preference_assessment"] = {"version": 1, "policy": "explicit_contrast_with_anonymous_quality_scores",
        "responses": deepcopy(assessments), "selected_indices": {"chosen": high, "rejected": low},
        "chosen_response_sha256": hashes[high], "rejected_response_sha256": hashes[low],
        "model_judgments_not_independent_truth": True}
    row.pop("learner_context_check", None)
    if assessments[0].get("learner_context_check") is not None:
        row["learner_context_check"] = deepcopy(assessments[0]["learner_context_check"])
    origins = record.get("preference_response_origins") or {}
    if origins:
        names = ("chosen", "rejected")
        row["preference_response_origins"] = {"chosen": origins.get(names[high]), "rejected": origins.get(names[low])}
    pending = bool(assessments[0].get("condition_pending"))
    stop = "condition_support_pending_review" if pending else None
    row["preference_assessment"]["stop_reason"] = stop
    return row, stop


def preference_pair_verdict(record, *, stop_reason=None):
    chosen = record["preference"]["chosen"]
    if stop_reason == "condition_support_pending_review":
        return {**deepcopy(chosen), "keep": False, "reason": chosen["reason"] + "; condition_support_pending_review"}
    return preference_release_feedback(record, chosen)


def rlaif_view(record, *, judge_role):
    """Project feedback from the same validated comparison, not a new verdict."""
    row = deepcopy(record)
    chosen, rejected = row["preference"]["chosen"], row["preference"]["rejected"]
    row["rlaif"] = {"criterion": "correctness", "chosen_feedback": chosen["reason"],
        "rejected_feedback": rejected["reason"], "judge": "jev", "judge_role": judge_role,
        "review_node": "review", "chosen_dimensions": deepcopy(chosen["scores"]),
        "rejected_dimensions": deepcopy(rejected["scores"])}
    return row


def contrastive_reasoning_issue(record, config):
    config = validate_preference_design(config)
    texts = ["\n".join(reasoning_text(message) for message in record[side]) for side in ("chosen", "rejected")]
    if any(not text.strip() for text in texts):
        return "preference_reasoning_required"
    if len(texts[1]) > config["negative_reasoning_max_chars"]:
        return "preference_negative_reasoning_too_long"
    if len(texts[1]) > len(texts[0]) * config["negative_reasoning_max_ratio"]:
        return "preference_negative_reasoning_not_shorter"
    return None


def validate_contrast_assessment(value, record, *, version=1):
    """Claims must quote actual answers; this binds, but does not prove, a judge."""
    fields = {"same_task", "opposite", "off_topic", "chosen_claim", "rejected_claim", "reason"}
    if version in {2, 3}:
        fields |= {"rationale_supports_answer", "rationale_quote"}
    if version == 3:
        fields |= {"topic_linked", "associative_drift", "topic_anchor", "drift_quote"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("invalid_preference_contrast_assessment")
    if any(type(value[key]) is not bool for key in ("same_task", "opposite", "off_topic")):
        raise ValueError("invalid_preference_contrast_assessment")
    if value["off_topic"] and (value["same_task"] or value["opposite"]) or value["opposite"] and not value["same_task"]:
        raise ValueError("inconsistent_preference_contrast_assessment")
    for side in ("chosen", "rejected"):
        quote = value[side + "_claim"]
        content = "\n".join(str(message.get("content", "")) for message in record[side])
        if not isinstance(quote, str) or not quote.strip() or quote not in content:
            raise ValueError("preference_contrast_quote_not_in_answer")
    if not isinstance(value["reason"], str) or not value["reason"].strip():
        raise ValueError("invalid_preference_contrast_assessment")
    if version in {2, 3}:
        quote = value["rationale_quote"]
        rationale = "\n".join(reasoning_text(message) for message in record["rejected"])
        if type(value["rationale_supports_answer"]) is not bool:
            raise ValueError("invalid_preference_contrast_assessment")
        if not isinstance(quote, str) or not quote.strip() or quote not in rationale:
            raise ValueError("preference_contrast_quote_not_in_rationale")
    if version == 3:
        if any(type(value[key]) is not bool for key in ("topic_linked", "associative_drift")):
            raise ValueError("invalid_preference_contrast_assessment")
        if value["associative_drift"] and (not value["topic_linked"] or not value["off_topic"]):
            raise ValueError("inconsistent_preference_contrast_assessment")
        for flag, key, texts in (
            ("topic_linked", "topic_anchor", [m.get("content", "") for m in record["prompt"] if m.get("role") == "user"]),
            ("associative_drift", "drift_quote", [m.get("content", "") for m in record["rejected"]]),
        ):
            quote = value[key]
            if not isinstance(quote, str) or (value[flag] and (not quote.strip() or not any(
                    isinstance(text, str) and quote in text for text in texts))) or (not value[flag] and quote):
                raise ValueError("preference_drift_quote_not_in_" + key)
    return deepcopy(value)


def contrastive_repair_contract(config, *, version=1):
    config = validate_preference_design(config)
    negative_rule = ("rejected 从原题真实概念或需求出发，经过错误联想逐渐偏离实际目标；不能突兀换成完全无关话题，也不能先答对再附闲话。"
        if config["mode"] == "associative_drift_short" else
        "rejected 必须明显答非所问：自然回答另一个无关问题，不能继续回答或解决当前问题；"
        "只给错结论、少答一部分、推脱或拒答，都不等于答非所问。"
        if config["mode"] == "off_topic_short" else
        "rejected 必须对同一核心问题给出与正确结论完全相反的判断或行动建议。")
    return (f"\n偏好设计模式 {config['mode']}：原问题、上下文、工具事实保持原样。chosen 必须正确且有充分的实际推导；"
        "先形成可检验的拟结论，再用必要的条件、边界情况、反例或计算核查，发现问题要修订拟结论。"
        "把必要的草稿与推导写在正例推理字段中，不用统一编号步骤，不用重复、空泛自问或纯翻译凑长。"
        + negative_rule + "保留与负例实际回答一致的极短推理。"
        "不要改原始 prompt、加条件、随机堆错、乱码或截断；不能把负例修成正确答案。"
        f"负例推理必须非空，不超过 {config['negative_reasoning_max_chars']} 字符，且不超过正例的 "
        f"{config['negative_reasoning_max_ratio']:.0%}。若长短比不满足，先检查正例是否缺少必要推导，不能用废话扩写。"
        "两侧已有推理字段都保留并改写；生成、评审、正负标签和这些制作规则不得写进训练正文。"
        + ("负例推理要自然解释它自己的回答为什么成立，不能评论“我没回答原问题”“未涉及 CSV”等偏题制作意图。" if version in {2, 3} else "")
        +
        "不能满足所选设计时 uncertain=true，保留待处理状态。\n")


def contrastive_relationship_issue(assessment, config):
    if assessment.get("rationale_supports_answer") is False:
        return "preference_negative_rationale_meta"
    mode = validate_preference_design(config)["mode"]
    if mode == "associative_drift_short":
        return None if (assessment.get("associative_drift") is True and assessment.get("topic_linked") is True
            and assessment["off_topic"] and not assessment["same_task"]) else "preference_associative_drift_required"
    if mode == "off_topic_short":
        return None if assessment["off_topic"] and not assessment["same_task"] else "preference_off_topic_answer_required"
    return None if assessment["same_task"] and assessment["opposite"] else "preference_opposite_answer_required"


def preserve_preference_response_history(original, updated):
    """A rewritten response is authored revision text, never a native trace."""
    origins = deepcopy(original.get("preference_response_origins") or {})
    history = deepcopy(original.get("preference_response_history") or [])
    for side in ("chosen", "rejected"):
        if candidate_digest(original[side]) == candidate_digest(updated[side]):
            continue
        history.append({"version": 1, "side": side, "response": deepcopy(original[side]),
            "reasoning_origin": origins.get(side), "response_sha256": candidate_digest(original[side]),
            "replacement_sha256": candidate_digest(updated[side]), "scope": "historical_preference_response"})
        origins[side] = "prompt_revision" if side == "chosen" else "prompt_contrastive_revision"
    if history:
        updated["preference_response_history"] = history
    if origins:
        updated["preference_response_origins"] = origins
