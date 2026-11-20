"""Forecast a learner's next observable submission using previously published evidence."""

import json

from lib.prompts.workflow_guided_answers import BEHAVIOR_PREDICTOR
from .guided_answers import inspect_answer


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


def prediction_due(protocol_version, previous_attempt, disclosures, *, received_full=False, solver_role="student"):
    if protocol_version != "4.10.7" or received_full or solver_role != "student" or not previous_attempt or not disclosures:
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


def prediction_messages(visible):
    return [{"role": "system", "content": BEHAVIOR_PREDICTOR},
            {"role": "user", "content": json.dumps(visible, ensure_ascii=False)}]


def _distinct_fields(pairs):
    data = dict(pairs)
    if len(data) != len(pairs):
        raise ValueError("ambiguous behavior forecast")
    return data


def prediction_guidance(response, visible, *, chinese):
    """Project the predictor's selection onto grounded directions. Its prose remains private."""
    if response.get("finish_reason") != "stop" or not response.get("model"):
        return None, None
    try:
        result = json.loads(response["content"], object_pairs_hook=_distinct_fields)
    except (ValueError, TypeError, KeyError):
        return None, None
    if (not isinstance(result, dict) or set(result) != set(BEHAVIOR_PREDICTION_SCHEMA["required"])
            or not all(isinstance(v, str) for v in result.values()) or not result["reason"].strip()):
        return None, None
    action, outcome = result["action"], result["outcome"]
    if (action not in _OUTCOMES or outcome not in (*_OUTCOMES[action], "unknown")
            or result["direction"] not in (*_DIRECTIONS, "none")):
        return None, None
    forecast = {"action": action, "outcome": outcome, "reason": result["reason"]}
    local = visible["current_local_task"]
    if ((action in {"report_local", "report_obstacle"} and not local) or (action == "submit_answer" and local)):
        return None, None
    direction, ref = result["direction"], result["source"]
    if outcome == "unknown" or direction not in _DIRECTIONS:
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
        if "unknown" in (predicted, observed):
            return "unknown"
        return "matched" if predicted == observed else "mismatched"

    return {"action": action, "outcome": outcome, "action_agreement": agreement("action", action),
            "outcome_agreement": agreement("outcome", outcome)}


def prediction_feedback(record):
    forecast = record["forecast"]
    return {"forecast": {k: forecast[k] for k in ("action", "outcome")} if forecast else None,
            "predictor_interpretation": {"author": "behavior_predictor", "status": "unverified_interpretation",
                                         "text": forecast["reason"]} if forecast else None,
            "student_guidance": record["student_guidance"], "observed_student_submission": record["observation"],
            "agreement_scope": "observable_action_and_outcome_categories_only",
            "published_disclosure_ordinal": record["disclosure_ordinal"]}
