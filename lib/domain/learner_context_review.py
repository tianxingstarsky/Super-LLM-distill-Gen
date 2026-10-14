"""Visibility checks cannot change the independent factual score."""


def learner_context_verdict(value):
    if (not isinstance(value, dict) or set(value) != {"keep", "context_complete", "leakage_free", "reason"}
            or any(type(value.get(key)) is not bool for key in ("keep", "context_complete", "leakage_free"))
            or not isinstance(value.get("reason"), str) or not value["reason"].strip()
            or len(value["reason"]) > 4000 or "\x00" in value["reason"] or "\ufffd" in value["reason"]
            or value["keep"] and not (value["context_complete"] and value["leakage_free"])):
        raise ValueError("invalid_learner_context_judge_schema")
    return value
