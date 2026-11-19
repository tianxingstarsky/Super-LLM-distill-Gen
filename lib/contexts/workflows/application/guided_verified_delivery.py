"""Have the solver deliver its checked calculation, with an auditable public answer."""

import re

from ..domain.guided_answers import number
from ..domain.guided_teacher_delivery import requested_delivery


def _delivery_messages(source, steps, previous=None, *, prior_solver_readouts=None):
    task = source.get("task_system", "")
    chinese = str(source.get("source", {}).get("language", "")).lower().startswith("zh") or any(
        "\u4e00" <= char <= "\u9fff" for char in source["question"])
    instruction = ("你已完成计算。请读出最后所求量的数值。保留符号和精度。"
                   if chinese else "Your calculation is complete. Return the numerical value of the final requested "
                   "quantity, with its sign and precision intact.")
    if prior_solver_readouts:
        instruction = ("计算步骤已经核实。请接手上一位作答者的数值交付，读出最终所求量，保留符号和精度。"
                       if chinese else "Take over the final readout of the checked calculations. Return the numerical "
                       "value of the requested final quantity, keeping its sign and precision.")
    record = "\n".join(step["text"] for step in steps)
    label = "已核实的计算" if chinese else "Checked calculations"
    last = steps[-1]
    result = f"{last['quantity']} = {last['value']} {last['unit']}".strip()
    observation = f"{label}\n{record}\n\n{result}"
    if prior_solver_readouts:
        label = "上一位作答者读错的两个数值" if chinese else "Previous solver's two incorrect readouts"
        observation = f"{label}: {', '.join(prior_solver_readouts)}\n\n{observation}"
    if previous is not None:
        correction = ("上一次提交的数值与最后所求量不同。请重新读出该量。"
                      if chinese else "The submitted value differs from the final checked quantity. Read that quantity again.")
        observation += f"\n{correction}"
    messages = [{"role": "system", "content": "\n\n".join(filter(None, (task, instruction)))},
                {"role": "user", "content": source["question"]}]
    if previous is not None:
        messages.append({"role": "assistant", "content": previous})
    return [*messages, {"role": "tool", "content": observation}]


def _checked_delivery(response, source, steps):
    raw = response["content"].strip()
    result = {"format_ok": False, "answer_matches": None, "answer": None, "issue": None}
    if response["finish_reason"] != "stop" or response.get("reasoning_content") or not raw:
        result["issue"] = "incomplete_calculation_readout"
    elif not re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+|/[0-9]+)?(?:[eE][+-]?[0-9]+)?", raw) or number(raw) is None:
        result["issue"] = "invalid_calculation_readout"
    else:
        matched = number(raw) == number(steps[-1]["value"]) == number(source["gold"]["value"])
        result.update(format_ok=True, answer_matches=matched, answer=raw,
                      issue=None if matched else "calculation_readout_mismatch")
    return result


def calculation_delivery_record(source, steps, index, response, *, prior=None, expected_solver=None,
                                continuation_model=None):
    role = "verified_answer_retry" if prior else "verified_answer"
    if continuation_model:
        role = "verified_strong_answer"
    reader = continuation_model or expected_solver
    if not response.get("model") or reader is not None and response["model"] != reader:
        check = {"format_ok": False, "answer_matches": None, "answer": None, "issue": "solver_identity_unverified"}
    else:
        check = _checked_delivery(response, source, steps)
    public = ""
    if check["answer_matches"]:
        public = response["content"].strip()
        if requested_delivery(source) == "worked_solution":
            last = steps[-1]
            conclusion = f"{last['quantity']}: {last['expression']} = {public} {last['unit']}."
            public = "\n".join([*(step["text"] for step in steps[:-1]), conclusion])
    record = {"index": index, "role": role, "receipt": f"{index:03}-{role}.json",
              "messages": _readout_request(source, steps, prior, continuation_model), "response": response,
              "check": check, "public_answer": public}
    if continuation_model:
        record["expected_reader"] = continuation_model
    if prior:
        record["prior"] = prior
    return record


def _readout_request(source, steps, prior, continuation_model):
    if continuation_model:
        readouts = [prior["prior"]["response"]["content"], prior["response"]["content"]]
        return _delivery_messages(source, steps, prior_solver_readouts=readouts)
    return _delivery_messages(source, steps, prior["response"]["content"] if prior else None)


def deliver_verified_calculation(request, source, steps, index, *, expected_solver=None, continuation_model=None):
    attempts = [("verified_answer", None), ("verified_answer_retry", None)]
    if continuation_model and continuation_model != expected_solver:
        attempts.append(("verified_strong_answer", continuation_model))
    delivery = None
    for role, stronger in attempts:
        reply = request(role, index, _readout_request(source, steps, delivery, stronger), None)
        delivery = calculation_delivery_record(source, steps, index, reply, prior=delivery,
                                               expected_solver=expected_solver, continuation_model=stronger)
        if delivery["check"]["issue"] != "calculation_readout_mismatch":
            break
    return delivery


def validate_completed_calculation(case, trusted_receipts, *, continuation_model=None):
    """Replay the model's recorded moves and the readout before producing a training row."""
    from .solve_guided_calculations import validated_calculation_steps

    course = case["calculation_tutoring"]
    plan = case["verified_calculation_plan"]
    if course["receipt_id"] != plan["receipt_id"] or course["receipt_id"] not in trusted_receipts:
        raise ValueError("guided calculation lost its independent source receipt")
    model = case["attempts"][0]["student"].get("model")
    if not model:
        raise ValueError("calculation first response has no verified solver identity")
    steps = validated_calculation_steps(case, course["actions"], expected_solver=model)
    if (not steps or steps != course["steps"]
            or [s["id"] for s in steps] != [s["id"] for s in plan["steps"]]):
        raise ValueError("guided calculation is incomplete or differs from its reviewed operations")
    delivery = course["final_delivery"]
    index = len(course["actions"]) + 1
    prior = delivery.get("prior")
    stronger = None
    if prior and prior.get("prior"):
        if not continuation_model or continuation_model == model:
            raise ValueError("guided calculation handoff has no distinct reader in the frozen run")
        stronger = continuation_model
    expected_prior = None
    if prior:
        for failed in (prior.get("prior"), prior):
            if failed is None:
                continue
            expected_prior = calculation_delivery_record(case, steps, index, failed["response"],
                                                         prior=expected_prior, expected_solver=model)
            if failed != expected_prior or expected_prior["check"]["issue"] != "calculation_readout_mismatch":
                raise ValueError("guided calculation retry has no verified prior mismatch")
    expected = calculation_delivery_record(case, steps, index, delivery["response"], prior=expected_prior,
                                           expected_solver=model, continuation_model=stronger)
    if (expected != delivery or expected["check"]["answer_matches"] is not True
            or case["messages"][-1] != {"role": "assistant", "content": expected["public_answer"],
                                        "reasoning_content": "\n".join(step["text"] for step in steps)}):
        raise ValueError("guided calculation delivery differs from its independently checked record")
    return steps
