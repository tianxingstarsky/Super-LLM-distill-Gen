"""Versioned rule coverage and literal evidence binding; no semantic truth oracle."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re


DEFAULT_REASONING_RULES = [
    {"id": "task", "requirement": "逐项核对当前用户目标、前文约束和指定语言；不得把任务替换为更容易的问题。", "allow_na": False},
    {"id": "draft", "requirement": "需要解题或设计时，推理正文须有实际计算、状态推演、方案草稿或关键解法；仅计划要做什么不算完成。简单事实查询或必要澄清可说明为何无须草稿。", "allow_na": True},
    {"id": "argument", "requirement": "决定答案的结论须有具体前提及支持桥梁；不得把相关当因果、局部当全部、缺少资料当不存在，也不得替错误补条件。", "allow_na": False},
    {"id": "check", "requirement": "至少核查一个可能改变结论的具体风险，说明核查对象、合法条件、实际结果及其对判断的影响；不虚构工具执行，不以检查通过代替证据。", "allow_na": False},
    {"id": "scope", "requirement": "答案须服从推理最终成立的条件、数值和边界；无法确认的关键点保留未解决，不暴露后台规则。前缀阶段检查拟结论及待作答范围，不假装已经有答案。", "allow_na": False},
]

CLAIM_RULES = [
    {"id": "units", "requirement": "逐一复算正文的差值、倍率和增幅，核对指标语义、量表、单位、基线与比较行；分数点不是自动等于百分点，不以一个正确算式覆盖其它数值。", "allow_na": True},
    {"id": "strength", "requirement": "逐一检查强结论的前提和范围，尝试满足原前提却使结论不成立的反例；不将可能变成必然、未报告变成未做，也不替作者补条件。", "allow_na": True},
]


def default_reasoning_checklist():
    return {"version": 2, "rules": deepcopy(DEFAULT_REASONING_RULES + CLAIM_RULES)}


def default_checklist_text():
    return "\n".join(("? " if r["allow_na"] else "") + r["requirement"] for r in default_reasoning_checklist()["rules"])


def checklist_from_text(text, *, version=2):
    if not isinstance(text, str) or len(text) > 32000:
        raise ValueError("invalid_reasoning_checklist")
    rules = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        optional = line.startswith("? ")
        requirement = line[2:].strip() if optional else line
        phases = None
        if version in (5, 6, 7) and requirement.startswith(("[prefix] ", "[final] ")):
            phase, requirement = requirement.split(" ", 1)
            phases = [phase[1:-1]]
        rules.append({"id": "r_" + hashlib.sha256((str(phases) + requirement if phases else requirement).encode()).hexdigest()[:16],
                      "requirement": requirement, "allow_na": optional, **({"phases": phases} if phases else {})})
    return validate_reasoning_checklist({"version": version, "rules": rules})


def validate_reasoning_checklist(config):
    if (not isinstance(config, dict) or set(config) - ({"style_admission", "evidence_policy", "batch_submission_version"} if config.get("version") == 6 else set()) != {"version", "rules"}
            or type(config["version"]) is not int or config["version"] not in (1, 2, 3, 4, 5, 6, 7)
            or not isinstance(config["rules"], list) or not 1 <= len(config["rules"]) <= 32):
        raise ValueError("invalid_reasoning_checklist")
    if "style_admission" in config and config["style_admission"] not in ("strict", "advisory"):
        raise ValueError("invalid_reasoning_checklist")
    if "evidence_policy" in config and (type(config["evidence_policy"]) is not int or config["evidence_policy"] not in (1, 2)):
        raise ValueError("invalid_reasoning_checklist")
    if "batch_submission_version" in config and (type(config["batch_submission_version"]) is not int
            or config["batch_submission_version"] != 1 or config.get("evidence_policy") != 2):
        raise ValueError("invalid_reasoning_checklist")
    ids = set()
    for rule in config["rules"]:
        if (not isinstance(rule, dict) or set(rule) - ({"phases"} if config["version"] in (5, 6, 7) else set()) != {"id", "requirement", "allow_na"}
                or not isinstance(rule["id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", rule["id"])
                or rule["id"] in ids or type(rule["allow_na"]) is not bool
                or not isinstance(rule["requirement"], str) or not rule["requirement"].strip()
                or len(rule["requirement"]) > 2000 or "\x00" in rule["requirement"]):
            raise ValueError("invalid_reasoning_checklist")
        if "phases" in rule and (not isinstance(rule["phases"], list) or not rule["phases"]
                or any(p not in ("prefix", "final") for p in rule["phases"])
                or len(set(rule["phases"])) != len(rule["phases"])):
            raise ValueError("invalid_reasoning_checklist")
        ids.add(rule["id"])
    return deepcopy(config)


def checklist_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def checklist_evidence_issue(rules, candidate, assessment):
    """Reject missing/duplicate rules and fabricated citations, not short prose."""
    if not isinstance(assessment, dict) or set(assessment) != {"checks"} or not isinstance(assessment["checks"], list):
        return "checklist_invalid_assessment"
    expected = {r["id"]: r for r in rules}
    seen = set()
    for check in assessment["checks"]:
        if (not isinstance(check, dict) or set(check) != {"rule_id", "status", "evidence", "reason"}
                or not isinstance(check["rule_id"], str) or check["rule_id"] not in expected
                or check["rule_id"] in seen or check["status"] not in ("met", "unmet", "not_applicable")
                or not isinstance(check["reason"], str) or not check["reason"].strip()
                or not isinstance(check["evidence"], list)):
            return "checklist_invalid_rule"
        seen.add(check["rule_id"])
        if check["status"] == "not_applicable" and not expected[check["rule_id"]]["allow_na"]:
            return "checklist_required_rule_waived"
        if check["status"] == "met" and not check["evidence"]:
            return "checklist_missing_evidence"
        for citation in check["evidence"]:
            if (not isinstance(citation, dict) or set(citation) != {"field", "quote"}
                    or citation["field"] not in ("reasoning", "answer")
                    or not isinstance(citation["quote"], str) or not citation["quote"].strip()
                    or citation["quote"] not in candidate.get(citation["field"], "")):
                return "checklist_evidence_not_in_candidate"
    if seen != set(expected):
        return "checklist_rule_coverage_missing"
    return None


def checklist_passed(assessment):
    return all(c["status"] != "unmet" for c in assessment["checks"])


def checklist_bound_to_record(record, audit):
    reasoning = record.get("reasoning")
    if isinstance(reasoning, list):
        reasoning = "\n\n".join(reasoning)
    answer = record.get("answer")
    if not isinstance(reasoning, str):
        messages = record.get("messages") or []
        last = messages[-1] if messages and messages[-1].get("role") == "assistant" else {}
        reasoning, answer = last.get("reasoning_content"), last.get("content")
    if not isinstance(reasoning, str):
        return False
    body = {"reasoning": reasoning}
    if audit.get("phase") == "final":
        if not isinstance(answer, str):
            return False
        body["answer"] = answer
    elif audit.get("phase") != "prefix":
        return False
    return (audit.get("candidate_sha256") == checklist_digest(body)
            and audit.get("rules_sha256") == checklist_digest(audit.get("rules")))
