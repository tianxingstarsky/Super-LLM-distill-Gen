"""Inspectable teaching explanations, separate from a teacher's hidden reasoning.

The trace exposes premises, the bridge supporting each result, and a targeted
check. Its local validation rejects malformed or plainly empty scaffolding;
it cannot establish factual truth, entailment, or honest tool execution. Those
remain obligations of an independent reviewer using the actual task/evidence.
"""
from __future__ import annotations

import json
import re
import unicodedata


AUTHORED_REASONING_VERSION = 1
AUTHORED_REASONING_JSON_CONTRACT = {
    "reasoning": "按用户指定风格新撰写的、可检查的教学解释",
    "answer": "回答当前任务的最终答案或必要澄清",
    "reasoning_trace": {
        "task": "当前需要完成的具体任务及约束",
        "premises": ["来自题面、实际对话或来源的具体依据；明确区分必要假设"],
        "steps": [{"basis": "本步所用的具体前提或前一步结果",
                   "inference": "该依据支持本步结果的规则、运算或语言解释桥梁",
                   "result": "本步实际得到的中间结果或结论"}],
        "checks": [{"check": "针对本任务某个具体风险的核对办法",
                    "result": "核对对象、实际结果与能够支持的结论；未验证则如实说明"}],
        "limitations": ["必要的适用条件、歧义或证据局限；确无额外边界时可为空数组"],
    },
}
AUTHORED_REASONING_PROTOCOL_INSTRUCTION = (
    "显式教学解释协议 v1：reasoning 是你依据当前任务新撰写的可检查解释，"
    "不是请求、恢复、复制或导出教师模型隐藏推理。遵守用户设定的表达风格。"
    "同时返回 reasoning_trace 供审计；它描述这份教学解释的论证结构，不能宣称是模型真实内部思维。"
    "task 写清实际目标；premises 列出具体依据并区分已知条件与必要假设；"
    "steps 的每一步都须写 basis、inference、result，明确依据为什么支持该结果，"
    "不能用‘分析/思考/得出结论’或重复答案替代支持桥梁。"
    "先识别需要的子任务再按依赖解决，检查是否遗漏关键前提或跨步；"
    "步骤数量与任务相称，简单任务可仅一步，不规定六步或最少字数，不凑步骤。"
    "checks 至少给出一项针对当前任务的实际核对，例如代回条件、核对单位/否定/来源、"
    "检查指代或信息充分性，并写具体结果；‘检查通过/已验证’不是可核查结果。"
    "没有实际工具记录时不得声称已经运行工具、实验或外部检索；概念核对须如实说明核对范围。"
    "limitations 写必要的适用条件、歧义与证据缺口；没有额外局限可为 []，不得编造局限或机械拒答。"
    "对话、澄清、条件回答和证据不足可使用语言推理，不强迫数学运算或假造验证。"
    "reasoning 与 answer 必须和 trace 中的依据、过程、边界一致；"
    "reasoning 应自然地呈现指定风格，不能把 trace 的 JSON 字段或固定审计栏目机械抄进训练回答。"
    "此协议只提供可审计结构，不保证内容正确，仍须独立评审。仅返回以下 JSON 契约："
    + json.dumps(AUTHORED_REASONING_JSON_CONTRACT, ensure_ascii=False, separators=(",", ":"))
)


def _normalised(text):
    return "".join(character for character in unicodedata.normalize("NFKC", text).casefold()
                   if character.isalnum() or character in "+−-*/=<>≠≤≥×÷%^_")


_VACUOUS = frozenset(_normalised(text) for text in (
    "分析", "思考", "推理", "推导", "计算", "检查", "验证", "总结", "结论", "结果", "依据", "前提",
    "具体依据", "具体前提", "已知条件", "本步依据", "题面", "参考答案", "支持桥梁", "中间结果", "最终结论", "得出结论",
    "根据已知条件", "根据以上分析", "按规则推导", "分析问题", "认真分析", "逐步思考",
    "一步一步思考", "进行计算", "得到答案", "答案正确", "合理", "正确", "通过", "已验证",
    "检查通过", "验证通过", "全部检查通过", "已经验证", "已经检查", "没有问题", "无问题", "符合要求", "与原文一致",
    "无需检查", "不需要检查", "检查答案", "检查答案正确性", "核对结果", "验证结果",
    "analysis", "think", "reason", "calculate", "check", "verify", "conclusion", "result",
    "premise", "basis", "inference", "given the information", "apply the rule", "think step by step",
    "analyze the problem", "therefore the answer", "checked", "verified", "passed", "correct",
    "all checks passed", "check the answer", "verify the answer", "no issues", "looks good",
    "TODO", "TBD", "N/A", "placeholder", "示例", "待填写", "略", "...", "无", "none",
))


def _contract_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _contract_strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _contract_strings(item)


# Echoing the displayed schema descriptions is not an authored explanation.
_VACUOUS |= frozenset(_normalised(text) for text in _contract_strings(AUTHORED_REASONING_JSON_CONTRACT))
_UNCERTAINTY = re.compile(
    r"无法(?:确定|判断|核实)|不能(?:确定|判断)|(?:缺少|缺失)(?:必要)?(?:条件|依据|证据|信息)|"
    r"(?:证据|信息|条件)(?:不足|不充分)|need (?:more information|clarification)|"
    r"cannot (?:determine|verify)|insufficient (?:evidence|information)", re.IGNORECASE)


def _concrete_text(value):
    return isinstance(value, str) and bool(value.strip()) and _normalised(value) not in _VACUOUS


def _text_list(value, *, allow_empty=False):
    return (isinstance(value, list) and (allow_empty or bool(value))
            and all(_concrete_text(item) for item in value))


def authored_reasoning_issue(candidate, *, answer_required=True) -> str | None:
    """Return a safe structural issue code, without claiming semantic proof.

    Call on the raw authored candidate, before projecting its text to training
    records. Additional candidate metadata is allowed, while the audit trace's
    fields follow the versioned protocol. Exact boilerplate and duplicate
    bridges are rejected without imposing a minimum explanation length.
    """
    if not isinstance(candidate, dict):
        return "authored_reasoning_invalid_candidate"
    if not _concrete_text(candidate.get("reasoning")):
        return "authored_reasoning_invalid_text"
    if answer_required and (not isinstance(candidate.get("answer"), str) or not candidate["answer"].strip()):
        return "authored_reasoning_invalid_answer"
    trace = candidate.get("reasoning_trace")
    if not isinstance(trace, dict):
        return "authored_reasoning_missing_trace"
    if set(trace) != {"task", "premises", "steps", "checks", "limitations"}:
        return "authored_reasoning_invalid_trace_fields"
    if not _concrete_text(trace["task"]):
        return "authored_reasoning_invalid_task"
    if not _text_list(trace["premises"]):
        return "authored_reasoning_invalid_premises"
    premise_keys = [_normalised(value) for value in trace["premises"]]
    if len(set(premise_keys)) != len(premise_keys):
        return "authored_reasoning_repeated_premise"
    if not isinstance(trace["steps"], list) or not trace["steps"]:
        return "authored_reasoning_invalid_steps"
    seen = set()
    for step in trace["steps"]:
        if not isinstance(step, dict) or set(step) != {"basis", "inference", "result"}:
            return "authored_reasoning_invalid_step_fields"
        if any(not _concrete_text(step[field]) for field in ("basis", "inference", "result")):
            return "authored_reasoning_vacuous_step"
        basis, inference, result = (_normalised(step[field]) for field in ("basis", "inference", "result"))
        if basis == result or inference in {basis, result}:
            return "authored_reasoning_circular_support"
        if (basis, inference, result) in seen:
            return "authored_reasoning_repeated_step"
        seen.add((basis, inference, result))
    if not isinstance(trace["checks"], list) or not trace["checks"]:
        return "authored_reasoning_missing_check"
    seen_checks = set()
    for check in trace["checks"]:
        if not isinstance(check, dict) or set(check) != {"check", "result"}:
            return "authored_reasoning_invalid_check_fields"
        if not _concrete_text(check["check"]) or not _concrete_text(check["result"]):
            return "authored_reasoning_vacuous_check"
        pair = (_normalised(check["check"]), _normalised(check["result"]))
        if pair[0] == pair[1]:
            return "authored_reasoning_vacuous_check"
        if pair in seen_checks:
            return "authored_reasoning_repeated_check"
        seen_checks.add(pair)
    if not _text_list(trace["limitations"], allow_empty=True):
        return "authored_reasoning_invalid_limitations"
    # A clear statement that a factual conclusion cannot be determined must
    # retain its boundary; subtler omissions require semantic review.
    if answer_required and not trace["limitations"] and _UNCERTAINTY.search(candidate["answer"]):
        return "authored_reasoning_missing_limitation"
    return None


def authored_rationale_issue(candidate) -> str | None:
    """Validate the reasoning-only phase without fabricating a final answer."""
    if not isinstance(candidate, dict) or set(candidate) != {"reasoning", "reasoning_trace"}:
        return "authored_rationale_invalid_fields"
    return authored_reasoning_issue(candidate, answer_required=False)
