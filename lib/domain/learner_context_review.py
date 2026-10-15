"""Visibility checks cannot change the independent factual score."""

import re


CONTEXT_FIELDS = ("keep", "context_complete", "leakage_free", "reason")


def learner_context_verdict(value):
    if (not isinstance(value, dict) or set(value) != set(CONTEXT_FIELDS)
            or any(type(value.get(key)) is not bool for key in ("keep", "context_complete", "leakage_free"))
            or not isinstance(value.get("reason"), str) or not value["reason"].strip()
            or len(value["reason"]) > 4000 or "\x00" in value["reason"] or "\ufffd" in value["reason"]
            or value["keep"] and not (value["context_complete"] and value["leakage_free"])):
        raise ValueError("invalid_learner_context_judge_schema")
    return value


def learner_context_format_feedback(value):
    """Describe a schema failure without replaying model text or hidden data.

    This feedback requests a new assessment of the same actual messages. It
    neither coerces an invalid decision nor turns an unassessed body into a
    pass. Arbitrary field names/values are not echoed as model instructions.
    """
    expected = set(CONTEXT_FIELDS)
    actual = set(value) if isinstance(value, dict) else set()
    unexpected = actual - expected
    safe_names = sorted(name for name in unexpected
                        if isinstance(name, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,39}", name))[:8]
    invalid = []
    if isinstance(value, dict):
        invalid.extend(name for name in CONTEXT_FIELDS[:3]
                       if name in value and type(value[name]) is not bool)
        reason = value.get("reason")
        if "reason" in value and (not isinstance(reason, str) or not reason.strip()
                or len(reason) > 4000 or "\x00" in reason or "\ufffd" in reason):
            invalid.append("reason")
        if value.get("keep") is True and (value.get("context_complete") is not True
                                         or value.get("leakage_free") is not True):
            invalid.append("keep_requires_both_checks")
    return {
        "code": "invalid_learner_context_judge_schema",
        "expected_fields": {"keep": "boolean", "context_complete": "boolean",
                            "leakage_free": "boolean", "reason": "nonempty string, at most 4000 characters"},
        "missing_fields": [name for name in CONTEXT_FIELDS if name not in actual],
        "unexpected_field_count": len(unexpected),
        "unexpected_fields": safe_names,
        "invalid_fields": invalid,
        "response_was_object": isinstance(value, dict),
        "instruction": ("上次响应未通过格式校验，不是可用评审。重新核对同一实际对话后，"
                        "只返回四个既定字段，不增加type、scores或解释字段；reason承载具体依据。"
                        "不要为了格式合格而改成通过；任一上下文或角色检查失败时keep=false。"),
    }
