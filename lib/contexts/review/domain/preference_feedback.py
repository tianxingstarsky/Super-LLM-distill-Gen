"""Explain the existing preference release gate without rewriting raw grades."""
from copy import deepcopy

from lib.contexts.workflows.contracts import preference_score_issue
from lib.domain.workflow_quality import canonical
from lib.domain.reasoning_fields import REASONING_FIELDS


PREFERENCE_REPAIR_CONTRACT = (
    "\n本次修正偏好对。record保持candidate的完整字段结构，prompt及其每条消息、角色、原文、工具事实必须原样保留。"
    "chosen是正例，rejected是用于对照的较差回答；不要把两份都改成同样正确、同样充分的答案。"
    "先确保chosen满足原问题和依据；若反馈说明正确性分差不足，则针对当前评价维度修正rejected的实际差异，"
    "不能只改详略、换说法或修改用户条件来制造差异。负例中的不足是训练对照，不作为正确答案回流。"
    "两侧已有的reasoning_content等推理字段都保留，必要时改写为与各自回答一致的解释，不删字段来过检。"
    "不将这些制作规则写入回答或推理，语义风格仍按本节点用户模板。\n")


def preference_release_feedback(record, chosen_verdict):
    """A valid grade is not proof of a valid pair; keep both original scores."""
    issue = preference_score_issue(record)
    if not issue:
        return chosen_verdict
    preference = record.get("preference", {})
    chosen = preference.get("chosen", {})
    rejected = preference.get("rejected", {})
    facts = {"dimension": preference.get("dimension"), "minimum_gap": preference.get("minimum_gap"),
             "chosen_correctness": chosen.get("correctness"), "rejected_correctness": rejected.get("correctness")}
    return {**deepcopy(chosen_verdict), "keep": False,
            "reason": chosen_verdict["reason"] + "; preference_release_guard: " + issue + "; " + canonical(facts)}


def preference_reasoning_issue(original, updated):
    """An edited preference may replace its rationale, never drop either side."""
    def has_text(value):
        return ((isinstance(value, str) and bool(value.strip()))
                or (isinstance(value, list) and bool(value)
                    and all(isinstance(part, str) for part in value) and any(part.strip() for part in value)))

    for side in ("chosen", "rejected"):
        before, after = original.get(side, []), updated.get(side, [])
        if not isinstance(before, list) or not isinstance(after, list):
            return "review_repair_invalid_schema"
        for index, message in enumerate(before):
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            for field in REASONING_FIELDS:
                if has_text(message.get(field)) and (index >= len(after) or not isinstance(after[index], dict)
                        or not has_text(after[index].get(field))):
                    return "review_repair_reasoning_missing"
    return None
