"""Let a solver propose executable arithmetic while keeping reference results private."""

import json

from ..domain.guided_calculation_plan import calculation_for_step, next_calculation_step, public_calculation_step
from ..domain.guided_student_calculations import matches_calculation_expression
from lib.prompts.workflow_guided_answers import CALCULATION_EXPRESSION, CALCULATION_SOLVER


EXPRESSION_INPUT = "solver-expression/v1"


def _submission_schema(remaining, operation_visible):
    def branch(action, count):
        expressions = ({"type": "array", "minItems": 1, "maxItems": count, "items": {
            "type": "string", "pattern": r"^[0-9()+*/. -]{1,160}$"}} if count else {"const": []})
        return {"type": "object", "additionalProperties": False, "required": ["action", "expressions"],
                "properties": {"action": {"const": action}, "expressions": expressions}}

    if operation_visible:
        return {"anyOf": [branch("use_shown_operation", 0), branch("request_hint", 0)]}
    return {"anyOf": [branch("calculate", 1), branch("finish", remaining), branch("request_hint", 0)]}


def _next_input(source, steps, previous_action, expression_input):
    if previous_action and previous_action["issue"] in {
            "incomplete_solver_action", "invalid_solver_action", "solver_identity_unverified"}:
        return None
    done = {item["id"] for item in steps}
    step = next_calculation_step(source, done)
    if step is None:
        return None
    shown = previous_action is not None and previous_action["step_id"] == step["id"]
    if shown and previous_action["operation_disclosed"]:
        return None
    move = public_calculation_step(source, step, done)
    if not shown:
        del move["operation"]
    packet = {"target": move, "verified_work": [
        {"text": item["text"], "operation_origin": item["operation_origin"], "value_source": item["value_source"]}
        for item in steps]}
    if shown:
        packet["previous_action_status"] = previous_action["issue"]
    direct_expression = expression_input and not shown
    system = CALCULATION_EXPRESSION if direct_expression else CALCULATION_SOLVER
    if source.get("task_system"):
        system = source["task_system"] + "\n\n" + system
    messages = [{"role": "system", "content": system}, {"role": "user", "content": source["question"]},
                {"role": "tool", "content": json.dumps(packet, ensure_ascii=False)}]
    remaining = len(source["verified_calculation_plan"]["steps"]) - len(steps)
    if direct_expression:
        expression = r"[0-9()+*/. -]{1,160}"
        extra = f"(;{expression}){{0,{remaining - 1}}}" if remaining > 1 else ""
        schema = {"type": "string", "pattern": rf"^(\?|{expression}{extra})$"}
    else:
        schema = _submission_schema(remaining, shown)
    return step, shown, messages, schema


def _apply_action(source, steps, action, expected_solver=None):
    """Advance only the prefix of submitted operations independently justified by the plan."""
    response = action["response"]
    if not response.get("model") or expected_solver is not None and response["model"] != expected_solver:
        return "solver_identity_unverified"
    if response.get("finish_reason") != "stop" or response.get("reasoning_content"):
        return "incomplete_solver_action"
    try:
        submitted = json.loads(response["content"])
    except (KeyError, TypeError, ValueError):
        return "invalid_solver_action"
    if action.get("input_format") == EXPRESSION_INPUT and not action["operation_disclosed"]:
        if not isinstance(submitted, str):
            return "invalid_solver_action"
        expressions = [] if submitted.strip() == "?" else [x.strip() for x in submitted.split(";")]
        choice = "request_hint" if not expressions else "calculate" if len(expressions) == 1 else "finish"
    else:
        if (not isinstance(submitted, dict) or set(submitted) != {"action", "expressions"}
                or not isinstance(submitted["expressions"], list)):
            return "invalid_solver_action"
        choice, expressions = submitted["action"], submitted["expressions"]
    plan = source["verified_calculation_plan"]["steps"]
    current = next_calculation_step(source, {s["id"] for s in steps})
    demonstration = choice == "request_hint" and not expressions and action["operation_disclosed"]
    if choice == "request_hint" and not expressions and not demonstration:
        return "solver_requested_guidance"
    if demonstration or (choice == "use_shown_operation" and not expressions and action["operation_disclosed"]):
        expressions = [calculation_for_step(source, current)[0]]
    elif action["operation_disclosed"] or not (choice == "calculate" and len(expressions) == 1
              or choice == "finish" and 1 <= len(expressions) <= len(plan) - len(steps)):
        return "invalid_solver_action"
    for expression in expressions:
        step = next_calculation_step(source, {s["id"] for s in steps})
        expected, value = calculation_for_step(source, step)
        if not matches_calculation_expression(expected, expression):
            return "expression_not_verified_for_quantity"
        shown = action["operation_disclosed"] and action["step_id"] == step["id"]
        origin = ("verified_teaching_demonstration" if demonstration else
                  "student_used_disclosed_operation" if shown else "student_proposal")
        steps.append({"id": step["id"], "quantity": step["quantity"], "unit": step["unit"],
                      "expression": expression, "value": str(value), "operation_disclosed": shown,
                      "operation_origin": origin,
                      "value_source": "exact_calculator", "student_receipt": action["receipt"],
                      "text": f"{step['quantity']}: {expression} = {value} {step['unit']}."})
    if demonstration:
        return "solver_needed_worked_demonstration"
    if choice == "finish" and len(steps) < len(plan):
        return "incomplete_solution"
    return None


def validated_calculation_steps(source, actions, *, expected_solver=None):
    """Rebuild trusted steps from recorded solver replies and the actual public inputs for each turn."""
    steps = []
    format_version = actions[0].get("input_format") if actions else None
    if format_version not in (None, EXPRESSION_INPUT):
        raise ValueError("unknown solver calculation input format")
    for offset, action in enumerate(actions, start=1):
        offer = _next_input(source, steps, actions[offset - 2] if offset > 1 else None, bool(format_version))
        if not offer:
            raise ValueError("unexpected solver action after the calculation stopped")
        step, shown, messages, _ = offer
        if (action["index"] != offset or action["role"] != "student_step" or action["receipt"] != f"{offset:03}-student_step.json"
                or step["id"] != action["step_id"] or shown is not action["operation_disclosed"]
                or action.get("input_format") != format_version or messages != action["request_messages"] or not action.get("response")):
            raise ValueError("calculation action differs from its expected public request")
        if _apply_action(source, steps, action, expected_solver) != action["issue"]:
            raise ValueError("recorded calculation decision differs from the solver response")
    return steps


def solve_guided_calculations(request, source, *, action_limit, expression_input=True, expected_solver=None):
    """Each new action gets the task, published move and checked results in a fresh conversation."""
    steps, actions = [], []
    plan = source["verified_calculation_plan"]
    while len(steps) < len(plan["steps"]) and (action_limit is None or len(actions) < action_limit):
        offer = _next_input(source, steps, actions[-1] if actions else None, expression_input)
        if not offer:
            break
        step, shown, messages, schema = offer
        index = len(actions) + 1
        response = request("student_step", index, messages, schema)
        action = {"index": index, "role": "student_step", "receipt": f"{index:03}-student_step.json",
                  "request_messages": messages, "response": response, "step_id": step["id"],
                  "operation_disclosed": shown}
        if expression_input:
            action["input_format"] = EXPRESSION_INPUT
        actions.append(action)
        action["issue"] = _apply_action(source, steps, action, expected_solver)
    return {"receipt_id": plan["receipt_id"], "actions": actions, "steps": steps}
