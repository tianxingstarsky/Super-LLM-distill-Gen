"""Sparse, evidence-bound review obligations; no inference or I/O in this module."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation, localcontext
import re

from .semantic_review import body_sections


CHANGE = re.compile(r"(?:提升|提高|增加|增长|增幅|下降|减少|差距|高出|百分点|percentage\s+points?|improv\w*|increase\w*|gain\w*|outperform\w*|decreas\w*)", re.I)
PERCENT = re.compile(r"\d+(?:\.\d+)?\s*(?:%|％|个?百分点|percentage\s+points?|percent)", re.I)
HYPOTHETICAL = re.compile(r"例如|比如|假设|假如|如果|不能说|不等于|错误|论文.{0,8}(?:称|写)|for example|suppose|hypothetical|incorrect|not mean|paper calls|from .+ to", re.I)


def reviewed_dialogue(context, candidate):
    """Every supervised historical assistant surface is reviewable, not evidence."""
    body = deepcopy(candidate)
    messages = context.get("prompt", context.get("messages", []))
    for index, message in enumerate(messages):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for name in ("content", "reasoning_content"):
            if isinstance(message.get(name), str) and message[name].strip():
                body[f"history_{index}_{name}"] = message[name]
    return body


def review_obligations(context, body):
    obligations = []
    for section in body_sections(body):
        text = section["text"]
        # Sentence windows prevent a routine proportion elsewhere in the same
        # paragraph inheriting a different sentence's change claim.
        for sentence in re.finditer(r"[^。！？!?;；\n]+(?:[。！？!?;；\n]|$)", text):
            for part in re.split(r"(?<!\d)\.(?!\d)", sentence[0]):
                if not (CHANGE.search(part) and (PERCENT.search(part) or
                        re.search(r"ROUGE[\s\S]*?(?:percentage\s+points?|百分点)", part, re.I))):
                    continue
                obligations.append({"id": "quantity:" + section["id"] + ":" + str(text.find(part, sentence.start())), "kind": "quantity",
                "section": section["id"], "field": section["field"], "quote": part,
                "question": "核验此段每个百分比变化的指标、比较行、基线及单位。百分点、相对增幅、ROUGE 分数点不可互换。正文措辞与图表冲突时回到图表轴和数据；只引用模糊的 by 10% 不足以核实。"})
    messages = context.get("prompt", context.get("messages", []))
    for i, message in enumerate(messages):
        if isinstance(message, dict) and message.get("role") == "assistant":
            obligations.append({"id": f"history:{i}", "kind": "history", "turn": i,
                "fields": [f for f in body if f.startswith(f"history_{i}_")],
                "question": "这一轮所有被监督的回答及推理是否正确？后文纠正不使错误的历史训练目标自动合格。用户说法不是助手事实标签。"})
    users = [(i, m["content"]) for i, m in enumerate(messages) if isinstance(m, dict)
             and m.get("role") == "user" and isinstance(m.get("content"), str)]
    if users and re.search(r"(?:资料|材料|文章|原文|段落|选段|两段|passage|excerpt|paragraph|provided text)", users[-1][1], re.I):
        obligations.append({"id": "visible_scope", "kind": "scope", "user_turn": users[-1][0],
            "question": "区分用户当时看得见的选段与教师全文。每个归属于选段的关键事实需有段内原句；额外知识要明确归属全文或一般建议。没有 only 限制也不能将全文事实说成该段事实。"})
    style = context.get("generation_style", {})
    if isinstance(style, dict) and style.get("reasoning_language") == "zh_en":
        obligations.append({"id": "bilingual_meaning", "kind": "bilingual",
            "question": "新推理中的关键中英文对应是否完整保留条件、否定、单位及强度，并解释其对本题推导的实际作用？引用实际释义和推导段落，英语标签、孤立术语表和声称已互译不算。无固定顺序、长度或语言比例。"})
    return obligations


def decimal_change(operation, left, right):
    """Operate on cited display-scale values. Never guess the metric's unit."""
    with localcontext() as ctx:
        ctx.prec = 40
        a, b = Decimal(left), Decimal(right)
        if not a.is_finite() or not b.is_finite():
            raise ValueError("nonfinite_quantity")
        if operation == "relative_percent":
            if a == 0:
                raise ValueError("zero_baseline")
            return (b - a) / a * 100
        if operation in ("percentage_points", "score_points"):
            return b - a
        raise ValueError("unknown_quantity_operation")


def quantity_issue(quantity, evidence_text, candidate_text):
    """Validate arithmetic and explicit unit assertions, not causal/row binding."""
    if not isinstance(quantity, dict) or set(quantity) != {"operation", "baseline", "current", "reported"}:
        return "quantity_normalization_missing"
    if any(not isinstance(quantity[k], str) for k in quantity):
        return "quantity_values_must_be_strings"
    for key in ("baseline", "current"):
        number = quantity[key]
        if not re.fullmatch(r"-?\d+(?:\.\d+)?", number) or not re.search(r"(?<![\d.])" + re.escape(number) + r"(?![\d.])", evidence_text):
            return "quantity_operand_not_in_external_evidence"
    reported = quantity["reported"]
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", reported) or not re.search(r"(?<![\d.])" + re.escape(reported) + r"(?![\d.])", candidate_text):
        return "reported_quantity_not_in_candidate"
    operation = quantity["operation"]
    if operation == "percentage_points" and not re.search(r"百分点|percentage\s+points?|\bpp\b", candidate_text, re.I):
        return "percentage_change_unit_ambiguous"
    if operation == "relative_percent" and not re.search(r"相对|relative|%|％|percent", candidate_text, re.I):
        return "relative_change_unit_missing"
    if operation == "score_points" and re.search(r"百分点|percentage\s+points?", candidate_text, re.I):
        return "score_points_are_not_percentage_points"
    if operation == "percentage_points" and re.search(
            r"ROUGE[^。！？;；\n]{0,48}?" + re.escape(reported) + r"\s*(?:个?百分点|percentage\s+points?)",
            candidate_text, re.I):
        return "score_points_are_not_percentage_points"
    # The scale must be visible in the evidence; a citation with numbers alone is insufficient.
    if operation == "percentage_points" and not re.search(r"%|％|percent|比例|百分", evidence_text, re.I):
        return "percentage_metric_evidence_missing"
    try:
        expected = decimal_change(operation, quantity["baseline"], quantity["current"])
        # Ordinary last-digit rounding, not a relaxed semantic tolerance.
        places = len(reported.partition(".")[2])
        if abs(expected - Decimal(reported)) > Decimal(5).scaleb(-places - 1):
            return "quantity_calculation_mismatch"
    except (InvalidOperation, ValueError, ZeroDivisionError):
        return "invalid_quantity_calculation"
    return None
