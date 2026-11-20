"""Check a learner's response to a published, independently attested local operation."""

import json
import re

from .guided_answers import contains_chat_control
from .guided_teaching_steps import verified_steps_digest


_BIT_OPERATIONS = {
    "fixed-position": {
        "画出位置表，把改动位置的旧值与当前生效的值分开。",
        "Mark the changed position and keep its old value separate from the value now in force."},
    "permitted-pair": {
        "给这两个位置各留一格，列出能满足它们相同或相异关系的取值组合，暂不补其余位置。",
        "Draw two slots for the linked positions. List only pairs satisfying the stated equality or inequality; leave the other positions blank."},
}


def _trial_hours(question, operation, cap):
    projects = (re.fullmatch(r"As a trial, take the projects ([A-Z]) and ([A-Z]) from the previous dependency\. "
                             r"Count each project's hours once and compare the sum with the new cap\. This checks this resource alone\.", operation)
                or re.fullmatch(r"取前面依赖中的 ([A-Z]) 和 ([A-Z]) 作为试算，把它们各自的工时只计一次，再对照新上限。这只检验这一项资源。", operation))
    if not projects or projects[1] == projects[2] or f"{projects[1]}→{projects[2]}" not in question:
        return None
    if not question.startswith(("The projects have (cost, hours, benefit) triples ", "项目（资金，工时，收益）是 ")):
        return None
    table = re.split(r"[.。]", question, maxsplit=1)[0]
    total = 0
    for project in projects.groups():
        matches = re.findall(rf"\b{project}=\(\d+,\s*(\d+),\s*\d+\)", table)
        if len(matches) != 1:
            return None
        total += int(matches[0])
    comparison = "<" if total < int(cap) else ">" if total > int(cap) else "="
    return [str(total), cap, comparison]


def local_step_target(source, disclosure, trusted, *, chinese):
    catalogue = source.get("verified_teaching_steps")
    if not catalogue or not disclosure:
        return None
    receipt = disclosure.get("source_step_receipt")
    if (receipt not in trusted or receipt != catalogue["receipt_id"] or
            receipt != verified_steps_digest(source["id"], source["question"], source["gold"], catalogue["steps"])):
        return None
    step = next((s for s in catalogue["steps"] if s["id"] == disclosure.get("source_step_id")), None)
    if not step or source["question"][slice(*step["span"])] != step["quote"]:
        return None
    if step["id"] in _BIT_OPERATIONS and step["operation"] not in _BIT_OPERATIONS[step["id"]]:
        return None
    quote = step["quote"]
    if step["id"] == "fixed-position":
        match = (re.fullmatch(r"The bit at position (\d+), formerly fixed to ([01]), is changed to ([01])", quote) or
                 re.fullmatch(r"原本第\s*(\d+)\s*位固定为\s*([01])\s*[，,]\s*现在改为\s*([01])", quote))
        label = "按顺序给出位置、旧值、当前值" if chinese else "Give the position, former bit and current bit, in that order"
    elif step["id"] == "updated-hours":
        match = (re.fullmatch(r"The hours limit falls from (\d+) to (\d+)", quote) or
                 re.fullmatch(r"工时上限由\s*(\d+)\s*降为\s*(\d+)", quote))
        label = ("按顺序给出这组试算项目的总工时、新上限和两者的比较符号（<、= 或 >）" if chinese else
                 "Give this trial pair's total hours, the new cap and the comparison sign (<, = or >), in that order")
    elif step["id"] == "permitted-pair":
        match = (re.fullmatch(r"The bits at positions (\d+) and (\d+) must be (equal|the same|different)", quote) or
                 re.fullmatch(r"第\s*(\d+)\s*位与第\s*(\d+)\s*位必须(相同|不同)", quote))
        label = "按引文的位置顺序列出允许的全部两位取值" if chinese else "List all allowed two-bit assignments in the quoted position order"
    else:
        return None
    if not match:
        return None
    if step["id"] == "permitted-pair":
        different = match[3] in {"different", "不同"}
        expected = [a+b for a in "01" for b in "01" if (a != b) == different]
    elif step["id"] == "updated-hours":
        expected = _trial_hours(source["question"], step["operation"], match[2])
        if expected is None:
            return None
    else:
        expected = list(match.groups())
    return {"step_id": step["id"], "receipt": receipt, "question_quote": quote, "operation": step["operation"],
            "operation_already_in_hint": step["operation"] in disclosure["text"],
            "request": label, "expected": expected}


def check_local_step(target, response, index):
    raw = response["content"]
    record = {k: v for k, v in target.items() if k != "expected"}
    record.update(status="unverified", independent_derivation_checked=False)
    if response["finish_reason"] != "stop" or response.get("reasoning_content") or not raw.strip():
        return {**record, "status": "incomplete_local_response"}, {}
    if contains_chat_control(raw):
        return record, {}
    end = len(raw.rstrip())
    submission = raw[:end].splitlines()[-1]
    try:
        action = json.loads(submission)
    except ValueError:
        return record, {}
    if isinstance(action, dict) and set(action) == {"blocked"} and isinstance(action["blocked"], str) and action["blocked"].strip():
        return {**record, "status": "blocked", "difficulty": action["blocked"]}, {}
    if not isinstance(action, list) or not all(isinstance(v, str) for v in action):
        return record, {}
    expected = target["expected"]
    matches = sorted(action) == sorted(expected) if target["step_id"] == "permitted-pair" else action == expected
    if not matches:
        return {**record, "status": "local_value_mismatch"}, {}
    key = "local:" + target["step_id"]
    finding = {"segment_id": f"a{index}.local", "author": "student", "text": submission,
               "source_span": [end - len(submission), end], "check_id": key,
               "meaning": " ".join((target["question_quote"], target["operation"], target["request"])),
               "reviewed_source": target["receipt"], "evidence_level": "checked_local_operation",
               "basis_id": "reviewed_progress_check", "depends_on": []}
    return {**record, "status": "verified_local_value"}, {key: finding}


def local_step_material(target):
    parts = [target["question_quote"]]
    if not target["operation_already_in_hint"]:
        parts.append(target["operation"])
    return "\n".join([*parts, target["request"]]) + "\n\n"
