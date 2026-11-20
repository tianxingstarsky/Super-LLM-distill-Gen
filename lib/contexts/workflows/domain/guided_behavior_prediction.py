"""Forecast a learner's next observable submission using previously published evidence."""

import json

from lib.prompts.workflow_guided_answers import BEHAVIOR_PREDICTOR, BEHAVIOR_PREDICTOR_4_10_8
from .guided_answers import inspect_answer
from .guided_protocol_versions import BEHAVIOR_VERSIONS, SOURCE_SELECTION_VERSIONS
from .guided_local_steps import local_submission_schema


_OUTCOMES = {"report_local": ("local_values_verified", "local_values_mismatch"),
             "report_obstacle": ("obstacle_submitted",),
             "submit_answer": ("answer_matches", "answer_mismatch"), "unknown": ("unknown",)}
_DIRECTIONS = {
    "apply_latest_disclosure": ("Apply this published operation to your current task.", "请把这条已公开的操作用于当前任务。"),
    "check_submission_fields": ("Check each part of your submission against this local task.", "请按这项局部任务逐项检查你的提交。"),
    "continue_checked_work": ("Use this checked part of your work when carrying out the current task.",
                              "执行当前任务时，可以使用这项已经核实的学生结果。"),
}
BEHAVIOR_PREDICTION_SCHEMA = {"type": "object", "additionalProperties": False,
    "required": ["action", "outcome", "reason", "direction", "source"],
    "properties": {"action": {"type": "string", "enum": list(_OUTCOMES)},
                   "outcome": {"type": "string", "enum": [o for os in _OUTCOMES.values() for o in os]},
                   "reason": {"type": "string"}, "direction": {"type": "string", "enum": [*_DIRECTIONS, "none"]},
                   "source": {"type": "string"}}}


_SOURCE_FIELDS = ["action", "outcome", "reason", "source"]


def prediction_due(protocol_version, previous_attempt, disclosures, *, received_full=False, solver_role="student"):
    if protocol_version not in BEHAVIOR_VERSIONS or received_full or solver_role != "student" or not previous_attempt or not disclosures:
        return False
    latest = disclosures[-1]
    return previous_attempt.get("disclosure") == latest and latest["issued_after_attempt"] == previous_attempt["attempt"]


def prediction_visible(question, disclosures, retained, findings, *, local_task):
    work = [{"id": "retained." + item["id"], "text": item["text"]} for item in retained]
    findings = list(findings)
    # The student renderer keeps one reviewed meaning per excerpt.
    meanings = {f["text"]: f["meaning"] for f in findings if f.get("check_id")}
    for ordinal, item in enumerate(findings, 1):
        fact = {"id": f"finding.{ordinal}", "text": item["text"]}
        if item["text"] in meanings:
            fact["meaning"] = meanings[item["text"]]
        work.append(fact)
    return {"original_question": question,
            "ordered_published_disclosures": [{"ordinal": d["ordinal"], "text": d["text"]} for d in disclosures],
            "confirmed_student_work": work, "current_local_task": local_task}


def _guidance_sources(visible):
    latest = visible["ordered_published_disclosures"][-1]
    sources = {f"disclosure.{latest['ordinal']}": "apply_latest_disclosure"}
    if visible["current_local_task"]:
        sources["local_task"] = "check_submission_fields"
    sources.update((item["id"], "continue_checked_work") for item in visible["confirmed_student_work"])
    sources[""] = "none"
    return sources


def prediction_schema(visible, protocol_version):
    if protocol_version == "4.10.7":
        return BEHAVIOR_PREDICTION_SCHEMA
    if protocol_version not in SOURCE_SELECTION_VERSIONS:
        raise ValueError("unsupported behavior prediction version")
    sources = _guidance_sources(visible)
    actions = ["report_local", "report_obstacle"] if visible["current_local_task"] else ["work_on_disclosure", "submit_answer"]
    actions.append("unknown")
    outcomes = dict.fromkeys(o for action in actions for o in _OUTCOMES.get(action, ("unknown",)))
    properties = {"action": {"type": "string", "enum": actions}, "outcome": {"type": "string", "enum": list(outcomes)},
                  "reason": {"type": "string"},
                  "source": {"type": "string", "enum": list(sources)}}
    return {**BEHAVIOR_PREDICTION_SCHEMA, "required": _SOURCE_FIELDS, "properties": properties}


def prediction_messages(visible, protocol_version="4.10.7"):
    if protocol_version in SOURCE_SELECTION_VERSIONS:
        packet = {**visible, "available_guidance_sources": _guidance_sources(visible)}
        return [{"role": "system", "content": BEHAVIOR_PREDICTOR_4_10_8},
                {"role": "user", "content": json.dumps(packet, ensure_ascii=False)}]
    return [{"role": "system", "content": BEHAVIOR_PREDICTOR},
            {"role": "user", "content": json.dumps(visible, ensure_ascii=False)}]


def _distinct_fields(pairs):
    data = dict(pairs)
    if len(data) != len(pairs):
        raise ValueError("ambiguous behavior forecast")
    return data


def _submitted_prediction(response, protocol_version):
    if response.get("finish_reason") != "stop" or not response.get("model"):
        return None
    try:
        result = json.loads(response["content"], object_pairs_hook=_distinct_fields)
    except (ValueError, TypeError, KeyError):
        return None
    fields = _SOURCE_FIELDS if protocol_version in SOURCE_SELECTION_VERSIONS else BEHAVIOR_PREDICTION_SCHEMA["required"]
    if (not isinstance(result, dict) or set(result) != set(fields)
            or not all(isinstance(v, str) for v in result.values()) or not result["reason"].strip()):
        return None
    return result


def prediction_guidance(response, visible, *, chinese, protocol_version="4.10.7"):
    """Project the predictor's selection onto grounded directions. Its prose remains private."""
    result = _submitted_prediction(response, protocol_version)
    if not result:
        return None, None
    action, outcome = result["action"], result["outcome"]
    outcomes = {**_OUTCOMES, "work_on_disclosure": ("unknown",)} if protocol_version in SOURCE_SELECTION_VERSIONS else _OUTCOMES
    if (action not in outcomes or outcome not in (*outcomes[action], "unknown")
            or (protocol_version == "4.10.7" and result["direction"] not in (*_DIRECTIONS, "none"))):
        return None, None
    forecast = {"action": action, "outcome": outcome, "reason": result["reason"]}
    local = visible["current_local_task"]
    if ((action in {"report_local", "report_obstacle"} and not local) or (action in {"submit_answer", "work_on_disclosure"} and local)):
        return None, None
    ref = result["source"]
    direction = _guidance_sources(visible).get(ref) if protocol_version in SOURCE_SELECTION_VERSIONS else result["direction"]
    if direction not in _DIRECTIONS or (protocol_version == "4.10.7" and outcome == "unknown"):
        return forecast, None
    latest = visible["ordered_published_disclosures"][-1]
    if direction == "apply_latest_disclosure" and ref == f"disclosure.{latest['ordinal']}":
        quote = latest["text"]
    elif direction == "check_submission_fields" and ref == "local_task" and local:
        quote = local
    elif direction == "continue_checked_work":
        quote = next((f["text"] for f in visible["confirmed_student_work"] if f["id"] == ref), None)
    else:
        quote = None
    if not quote:
        return forecast, None
    frame = {"origin": "behavior_predictor_guidance", "direction": direction, "source": ref,
             "quoted_visible_evidence": quote, "instruction": _DIRECTIONS[direction][1 if chinese else 0]}
    return forecast, {"role": "user", "content": json.dumps(frame, ensure_ascii=False)}


def place_student_guidance(guidance, messages, target, schema, *, single_task=False):
    """Locate the predictor's selected signed task in the already constructed student request."""
    if guidance is None:
        return None, {"status": "no_validated_selection"}
    frame = json.loads(guidance["content"])
    placement = {"status": "appended", "source": frame["source"], "direction": frame["direction"],
                 "positions": [{"message_index": len(messages), "role": "user", "json_pointer": "/quoted_visible_evidence"}]}
    if (not target or schema != local_submission_schema(target)
            or [m["role"] for m in messages] != ["system", "user"]):
        return guidance, placement
    try:
        packet = json.loads(messages[1]["content"], object_pairs_hook=_distinct_fields)
    except (TypeError, ValueError):
        return guidance, placement
    instruction = target["request"]
    system = messages[0]["content"]
    if (not isinstance(packet, dict) or packet.get("origin") != "tutoring_program"
            or packet.get("current_local_task") != instruction or not system.endswith("\n\n" + instruction)):
        return guidance, placement
    if single_task and frame["direction"] == "apply_latest_disclosure" and isinstance(packet.get("public_reference"), dict):
        history = packet["public_reference"].get("quoted_teacher_disclosures")
        if (isinstance(history, list) and history and isinstance(history[-1], dict)
                and frame["source"] == f"disclosure.{history[-1].get('ordinal')}"
                and frame["quoted_visible_evidence"] == history[-1].get("text")):
            return None, {**placement, "status": "already_in_public_reference", "positions": [
                {"message_index": 1, "role": "user", "json_pointer": f"/public_reference/quoted_teacher_disclosures/{len(history)-1}/text"}]}
    if (frame["source"] != "local_task" or frame["direction"] != "check_submission_fields"
            or frame["quoted_visible_evidence"] != instruction):
        return guidance, placement
    return None, {**placement, "status": "already_in_current_request", "positions": [
        {"message_index": 0, "role": "system", "content_span": [len(system) - len(instruction), len(system)]},
        {"message_index": 1, "role": "user", "json_pointer": "/current_local_task"}]}


def observe_prediction(forecast, attempt, *, question, gold, expected_model=None):
    student = attempt["student"]
    action, outcome = "unknown", "unknown"
    if (student.get("model") and (expected_model is None or student["model"] == expected_model)
            and student.get("finish_reason") == "stop" and not student.get("reasoning_content")):
        local = attempt.get("local_step")
        if local:
            local_observations = {"verified_local_value": ("report_local", "local_values_verified"),
                                  "local_value_mismatch": ("report_local", "local_values_mismatch"),
                                  "blocked": ("report_obstacle", "obstacle_submitted")}
            action, outcome = local_observations.get(local["status"], (action, outcome))
        elif gold is not None:
            check = inspect_answer(student["content"], gold, question=question, parse_count_sentence=True, marked_finals=True)
            if check.get("format_ok") and type(check.get("answer_matches")) is bool:
                action = "submit_answer"
                outcome = "answer_matches" if check["answer_matches"] else "answer_mismatch"

    def agreement(field, observed):
        predicted = (forecast or {}).get(field, "unknown")
        # A delivered answer can include intermediate work. Without an independent check
        # of that specific work, the delivery alone cannot establish or contradict it.
        if field == "action" and predicted == "work_on_disclosure":
            return "unknown"
        if "unknown" in (predicted, observed):
            return "unknown"
        return "matched" if predicted == observed else "mismatched"

    return {"action": action, "outcome": outcome, "action_agreement": agreement("action", action),
            "outcome_agreement": agreement("outcome", outcome)}


def prediction_feedback(record):
    forecast = record["forecast"]
    interpretation = forecast
    if not forecast and record.get("protocol_version") in SOURCE_SELECTION_VERSIONS:
        interpretation = _submitted_prediction(record["response"], record["protocol_version"])
    return {"forecast": {k: forecast[k] for k in ("action", "outcome")} if forecast else None,
            "predictor_interpretation": {"author": "behavior_predictor", "status": "unverified_interpretation",
                                         "text": interpretation["reason"]} if interpretation else None,
            "student_guidance": record["student_guidance"], "observed_student_submission": record["observation"],
            **({"guidance_delivery": record["guidance_delivery"]} if "guidance_delivery" in record else {}),
            "agreement_scope": "observable_action_and_outcome_categories_only",
            "published_disclosure_ordinal": record["disclosure_ordinal"]}
