"""Explicit cost/coverage tradeoff for restyling; never a correctness certificate."""
from .dialogue import dialogue_digest
from lib.domain.workflow_quality import accepted, verdict


def validate_cot_review_policy(value):
    if (not isinstance(value, dict) or set(value) != {"mode", "sample_percent"}
            or value["mode"] not in ("sampled", "strict")
            or type(value["sample_percent"]) is not int
            or not 1 <= value["sample_percent"] <= 100):
        raise ValueError("invalid_cot_review_policy")
    return dict(value)


def cot_review_plan(config, sample):
    policy = config.get("review_policy")
    if policy is None:
        return None  # Historical recipes retain their original path and receipts.
    identity = dialogue_digest({"source_id": sample.get("source_id"), "messages": sample.get("messages")})
    # Content-bound sampling is stable across resume and does not load the batch.
    bucket = int(identity[:16], 16) % 10000
    selected = policy["mode"] == "strict" or bucket < policy["sample_percent"] * 100
    return {"version": 1, **policy, "input_sha256": identity,
            "selected": selected, "semantic_status": "pending" if selected else "not_assessed",
            "selection": "content_hash_v1", "bucket": bucket,
            "coverage": "individual" if selected else "local_checks_only"}


def known_original_rejection(sample):
    """Negative prior evidence is a stop, never an upstream approval shortcut."""
    if sample.get("status") == "quarantined":
        return "cot_original_previously_rejected"
    prior = sample.get("judge")
    if prior is not None:
        try:
            if not accepted(verdict(prior)):
                return "cot_original_previously_rejected"
        except (ValueError, KeyError, TypeError):
            return "cot_original_review_invalid"
    return None


SAMPLED_REVIEW_INSTRUCTION = """本次是抽中的内容核验。完整检查用户任务、真实历史、答案和推理中的实质错误；原答案未变不是正确证据。
事实、数值单位、关键条件、因果强度、来源范围、角色归属和用户明确的语言/格式要求仍必须成立；不能替错误论证补条件。未证实和已证伪要区分。
只因段落次序、未显式写出草稿/反问、措辞不够优美、没有逐句互译或没有达到隐含篇幅偏好，不应否决。互译看是否保留含义与条件，不按次数或比例打分。用户明确要求仍需满足。
评审改写后的实际 reasoning 和 answer，分清字段；审计记录不是正文也不是真值。理由只写影响结论的具体缺陷或有意义的改进建议，不复述整份自检清单。"""
