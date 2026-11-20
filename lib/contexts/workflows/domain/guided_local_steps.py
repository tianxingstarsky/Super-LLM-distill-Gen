"""Check a learner's response to a published, independently attested local operation."""

import json
import re
from decimal import Decimal

from .guided_answers import contains_chat_control
from .guided_teaching_steps import verified_steps_digest
from lib.prompts.workflow_guided_answers import SCOPED_LOCAL_STUDENT, SCOPED_LOCAL_STUDENT_ZH


LOCAL_SUBMISSION_SCHEMA = {"anyOf": [
    {"type": "object", "additionalProperties": False, "required": ["action", "values", "explanation"],
     "properties": {"action": {"const": "report"}, "values": {"type": "array", "items": {"type": "string"}},
                    "explanation": {"type": "string"}}},
    {"type": "object", "additionalProperties": False, "required": ["action", "difficulty"],
     "properties": {"action": {"const": "blocked"}, "difficulty": {"type": "string"}}},
]}

# Fields come from the operation itself. A set of allowed bit assignments has
# unknown cardinality for the learner and keeps the open collection contract.
_FIXED_VALUE_FIELDS = {
    "fixed-position": {"position": "integer", "former_bit": "integer", "current_bit": "integer"},
    "updated-hours": {"total_hours": "integer", "new_limit": "integer", "comparison": "string"},
}


def local_submission_schema(target):
    fields = target.get("submission_fields")
    if not fields:
        return LOCAL_SUBMISSION_SCHEMA
    report, blocked = LOCAL_SUBMISSION_SCHEMA["anyOf"]
    values = {"type": "object", "additionalProperties": False, "required": list(fields),
              "properties": {name: {"type": kind} for name, kind in fields.items()}}
    return {"anyOf": [{**report, "properties": {**report["properties"], "values": values}}, blocked]}


def scoped_local_messages(source, target, guidance, chinese, experience=""):
    instruction = SCOPED_LOCAL_STUDENT_ZH if chinese else SCOPED_LOCAL_STUDENT
    materials = {"original_user_question": source["question"]}
    if source.get("task_system"):
        materials["original_task_instructions"] = source["task_system"]
    packet = {"origin": "tutoring_program", "quoted_source_material": materials,
              "published_guidance_and_established_work": guidance, "current_local_task": target["request"]}
    return [{"role": "system", "content": "\n\n".join(filter(None, (experience, instruction, target["request"])))},
            {"role": "user", "content": json.dumps(packet, ensure_ascii=False)}]


def dispatched_local_guidance(payload, source, target, chinese):
    """Recover only the public context of a source-bound program request with its native submit contract."""
    messages = payload["messages"]
    if [m.get("role") for m in messages] != ["system", "user"]:
        raise ValueError("the scoped local task requires a program request and attributed original materials")
    packet = json.loads(messages[1]["content"])
    guidance = packet.get("published_guidance_and_established_work") if isinstance(packet, dict) else None
    if not isinstance(guidance, str):
        raise ValueError("the scoped local task lacks its public guidance")
    expected = scoped_local_messages(source, target, guidance, chinese)
    contract = payload.get("response_format", {}).get("json_schema", {})
    if (messages[1] != expected[1] or not messages[0]["content"].endswith(expected[0]["content"])
            or contract.get("schema") != local_submission_schema(target) or contract.get("strict") is not True
            or payload["response_format"].get("type") != "json_schema"):
        raise ValueError("scoped local request differs from its original task, role or response schema")
    return guidance


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


def local_step_target(source, disclosure, trusted, *, chinese, scoped=False, fixed_fields=False):
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
        if scoped:
            label = ("按顺序给出这组试算项目的总工时、新上限和两者的比较符号" if chinese else
                     "Give this trial pair's total hours, the new cap and their comparison sign, in that order")
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
    fields = _FIXED_VALUE_FIELDS.get(step["id"]) if fixed_fields else None
    if fields:
        names = ", ".join(fields)
        label += (f"。在 values 的各字段中分别提交：{names}。" if chinese else
                  f". Submit each value separately in its named field in values: {names}.")
    return {"step_id": step["id"], "receipt": receipt, "question_quote": quote, "operation": step["operation"],
            "operation_already_in_hint": step["operation"] in disclosure["text"],
            "request": label, "expected": expected, **({"submission_fields": dict(fields)} if fields else {})}


def _distinct_members(pairs):
    members = dict(pairs)
    if len(members) != len(pairs):
        raise ValueError("duplicate member in submitted local values")
    return members


def _scoped_action(raw, fields):
    """Read the field and its actual character range without treating the explanation as checked work."""
    decoder = json.JSONDecoder(object_pairs_hook=_distinct_members if fields else None,
                               parse_float=Decimal if fields else float)
    value = decoder.decode(raw)
    if not isinstance(value, dict):
        return None, 0, 0
    # Walk the validated object's top-level members. Escaped names, multiline
    # arrays and strings quoting JSON examples retain their correct source spans.
    space = re.compile(r"[ \r\n\t]*")
    offset = space.match(raw, raw.index("{") + 1).end()
    members = {}
    while raw[offset] != "}":
        name, end = decoder.raw_decode(raw, offset)
        if name in members:
            return None, 0, 0
        colon = space.match(raw, end).end()
        start = space.match(raw, colon + 1).end()
        _, end = decoder.raw_decode(raw, start)
        members[name] = (start, end)
        offset = space.match(raw, end).end()
        if raw[offset] == ",":
            offset = space.match(raw, offset + 1).end()
    if set(value) == {"action", "difficulty"} and value["action"] == "blocked":
        return {"blocked": value["difficulty"]}, 0, 0
    if (set(value) != {"action", "values", "explanation"} or value["action"] != "report"
            or not isinstance(value["explanation"], str)):
        return None, 0, 0
    submitted = value["values"]
    if fields:
        if not isinstance(submitted, dict) or submitted.keys() != fields.keys():
            return None, 0, 0
        for name, kind in fields.items():
            part = submitted[name]
            if kind == "integer" and not (type(part) is int or isinstance(part, Decimal) and part == part.to_integral_value()):
                return None, 0, 0
            if kind == "string" and not isinstance(part, str):
                return None, 0, 0
        submitted = [submitted[name] for name in fields]
    elif not isinstance(submitted, list):
        return None, 0, 0
    start, end = members["values"]
    return submitted, start, end


def check_local_step(target, response, index, *, scoped=False):
    raw = response["content"]
    record = {k: v for k, v in target.items() if k != "expected"}
    record.update(status="unverified", independent_derivation_checked=False)
    if response["finish_reason"] != "stop" or response.get("reasoning_content") or not raw.strip():
        return {**record, "status": "incomplete_local_response"}, {}
    if contains_chat_control(raw):
        return record, {}
    end = len(raw.rstrip())
    submission = raw[:end].splitlines()[-1]
    start = end - len(submission)
    try:
        if scoped:
            action, start, end = _scoped_action(raw, target.get("submission_fields"))
            submission = raw[start:end]
        else:
            action = json.loads(submission)
    except ValueError:
        return record, {}
    if isinstance(action, dict) and set(action) == {"blocked"} and isinstance(action["blocked"], str) and action["blocked"].strip():
        return {**record, "status": "blocked", "difficulty": action["blocked"]}, {}
    fields = target.get("submission_fields")
    if not isinstance(action, list) or not fields and not all(isinstance(v, str) for v in action):
        return record, {}
    expected = target["expected"]
    if fields:
        expected = [int(v) if kind == "integer" else v for kind, v in zip(fields.values(), expected)]
    matches = sorted(action) == sorted(expected) if target["step_id"] == "permitted-pair" else action == expected
    if not matches:
        return {**record, "status": "local_value_mismatch"}, {}
    key = "local:" + target["step_id"]
    finding = {"segment_id": f"a{index}.local", "author": "student", "text": submission,
               "source_span": [start, end], "check_id": key,
               "meaning": " ".join((target["question_quote"], target["operation"], target["request"])),
               "reviewed_source": target["receipt"], "evidence_level": "checked_local_operation",
               "basis_id": "reviewed_progress_check", "depends_on": []}
    return {**record, "status": "verified_local_value"}, {key: finding}


def local_step_material(target):
    parts = [target["question_quote"]]
    if not target["operation_already_in_hint"]:
        parts.append(target["operation"])
    return "\n".join([*parts, target["request"]]) + "\n\n"
