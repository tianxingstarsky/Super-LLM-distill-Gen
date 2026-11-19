import copy
import json

import pytest

from lib.contexts.workflows.application.solve_guided_calculations import (
    solve_guided_calculations, validated_calculation_steps,
)
from lib.contexts.workflows.domain.guided_answers import validate_source
from lib.contexts.workflows.domain.guided_calculation_plan import PLAN_SCHEMA, calculation_plan_digest


def span(text, piece):
    start = text.index(piece)
    return [start, start + len(piece)]


@pytest.fixture
def apple_problem():
    question = ("There are 3 bags, each containing 4 apples, and 5 loose apples. "
                "How many apples are there in total? Give only the number.")
    proof = "The bags contain <<3*4=12>>12 apples. Adding the loose apples gives <<12+5=17>>17."
    source = {"id": "apples", "question": question, "source_reasoning": proof, "gold": {"kind": "number", "value": "17"},
              "answer_requirement": "final_only", "direct_answer_syntax": "number"}
    steps = [
        {"id": "bags", "quantity": "apples in the bags", "unit": "apples", "question_span": span(question, "each containing 4 apples"),
         "equation_span": span(proof, "3*4=12"), "inputs": [{"question_span": span(question, "3")}, {"question_span": span(question, "4")}]},
        {"id": "total", "quantity": "total apples", "unit": "apples", "question_span": span(question, "5 loose apples"),
         "equation_span": span(proof, "12+5=17"), "inputs": [{"step_id": "bags"}, {"question_span": span(question, "5")}]},
    ]
    receipt = calculation_plan_digest(source, steps)
    source["verified_calculation_plan"] = {"schema": PLAN_SCHEMA, "receipt_id": receipt, "steps": steps}
    return validate_source(source, allow_calculation_plan=True, trusted_step_receipts={receipt})


def solver(submissions, observed):
    def request(role, index, messages, schema):
        assert role == "student_step" and index == len(observed) + 1
        assert [m["role"] for m in messages] == ["system", "user", "tool"]
        observed.append(json.loads(messages[-1]["content"]))
        choice, expressions = submissions[index - 1]
        submitted = (";".join(expressions) if expressions else "?") if schema.get("type") == "string" else {
            "action": choice, "expressions": expressions}
        return {"content": json.dumps(submitted), "model": "solver",
                "finish_reason": "stop", "reasoning_content": ""}
    return request


def test_partial_solution_is_reused_and_shown_operation_never_claimed_as_unaided(apple_problem):
    seen = []
    request = solver([("finish", ["4*3", "14+3"]), ("request_hint", []), ("use_shown_operation", [])], seen)
    case = solve_guided_calculations(request, apple_problem, action_limit=None)
    assert len(case["actions"]) == 3 and [a["issue"] for a in case["actions"]] == [
        "expression_not_verified_for_quantity", "solver_requested_guidance", None]
    assert "operation" not in seen[0]["target"] and "operation" not in seen[1]["target"]
    assert "14+3" not in json.dumps(seen[1]) and seen[1]["verified_work"] == [
        {"text": "apples in the bags: 4*3 = 12 apples.", "operation_origin": "student_proposal",
         "value_source": "exact_calculator"}]
    assert seen[2]["target"]["operation"] == "12+5" and "17" not in json.dumps(seen[2])
    assert [s["operation_disclosed"] for s in case["steps"]] == [False, True]
    assert [s["value"] for s in case["steps"]] == ["12", "17"]
    assert validated_calculation_steps(apple_problem, case["actions"]) == case["steps"]
    forged = copy.deepcopy(case["actions"])
    forged[-1]["operation_disclosed"] = False
    with pytest.raises(ValueError, match="expected public request"):
        validated_calculation_steps(apple_problem, forged)


def test_wrong_expression_with_the_right_value_does_not_advance_and_retries_stop(apple_problem):
    seen = []
    case = solve_guided_calculations(solver([("calculate", ["2*6"]), ("finish", ["2*6", "12+5"])], seen),
                                     apple_problem, action_limit=None)
    assert case["steps"] == []
    assert len(seen) == 2 and seen[1]["target"]["operation"] == "3*4"
    assert [a["issue"] for a in case["actions"]] == ["expression_not_verified_for_quantity", "invalid_solver_action"]
    assert validated_calculation_steps(apple_problem, case["actions"]) == []


def test_bounded_run_honors_one_remaining_action_and_can_finish_without_future_hints(apple_problem):
    limited = solve_guided_calculations(solver([("request_hint", [])], []), apple_problem, action_limit=1)
    assert len(limited["actions"]) == 1 and not limited["steps"]
    seen = []
    completed = solve_guided_calculations(solver([("finish", ["3*4", "5+12"])], seen), apple_problem, action_limit=1)
    assert len(seen) == 1 and "total apples" not in json.dumps(seen[0])
    assert [s["value"] for s in completed["steps"]] == ["12", "17"]
    assert not any(s["operation_disclosed"] for s in completed["steps"])


def test_requested_demonstration_discloses_one_result_with_its_actual_author(apple_problem):
    seen = []
    case = solve_guided_calculations(solver([("request_hint", []), ("request_hint", []),
                                             ("calculate", ["12+5"])], seen), apple_problem, action_limit=None)
    assert case["actions"][1]["issue"] == "solver_needed_worked_demonstration"
    assert case["steps"][0]["operation_origin"] == "verified_teaching_demonstration"
    assert seen[2]["verified_work"][0]["operation_origin"] == "verified_teaching_demonstration"
    assert "12 apples" in seen[2]["verified_work"][0]["text"] and "17" not in seen[2]["verified_work"][0]["text"]
    assert case["steps"][1]["operation_origin"] == "student_proposal"
    assert validated_calculation_steps(apple_problem, case["actions"]) == case["steps"]


def test_provider_contract_forbids_hint_with_calculation_or_multiple_single_step_proposals():
    from jsonschema import Draft202012Validator
    from lib.contexts.workflows.application.solve_guided_calculations import _submission_schema
    weak, shown = [Draft202012Validator(_submission_schema(3, visible)) for visible in (False, True)]
    assert not weak.is_valid({"action":"request_hint", "expressions":["1+29"]})
    assert not weak.is_valid({"action":"calculate", "expressions":["/4", "200/4"]})
    assert not shown.is_valid({"action":"calculate", "expressions":["200/4"]})
    assert all(shown.is_valid({"action": choice, "expressions":[]}) for choice in ("use_shown_operation", "request_hint"))


def test_truncated_solver_action_cannot_trigger_a_demonstration(apple_problem):
    def request(role, index, messages, schema):
        return {"content": '{"action":"request_hint","expressions":[]}', "model": "solver",
                "reasoning_content": "", "finish_reason": "length"}
    case = solve_guided_calculations(request, apple_problem, action_limit=None)
    assert not case["steps"] and len(case["actions"]) == 1 and case["actions"][0]["issue"] == "incomplete_solver_action"


def test_worked_task_requires_verified_derivation_and_fresh_delivery_despite_correct_estimate(apple_problem):
    from lib.contexts.workflows.application.generate_guided_answer import GenerateGuidedAnswer

    task = dict(apple_problem, question=apple_problem["question"].replace("Give only the number.", "Show your work."),
                answer_requirement="worked_solution")
    task.pop("direct_answer_syntax")
    plan = copy.deepcopy(task["verified_calculation_plan"])
    receipt = calculation_plan_digest(task, plan["steps"])
    task["verified_calculation_plan"] = dict(plan, receipt_id=receipt)
    task = validate_source(task, allow_calculation_plan=True, trusted_step_receipts={receipt})
    roles = []

    def request(role, index, messages, schema):
        roles.append(role)
        if role == "student_step":
            return solver([("finish", ["4*3", "12+5"])], [])(role, index, messages, schema)
        assert role in {"student", "verified_answer"}
        return {"content": "17", "model": "solver", "finish_reason": "stop", "reasoning_content": ""}

    case = GenerateGuidedAnswer(request).run(task, {"version": "test"}, 2, protocol_version="4.10.0",
                                            trusted_solution_receipts={receipt})
    assert roles == ["student", "student_step", "verified_answer"]
    assert case["status"] == "accepted" and case["training_route"] == "guided_reasoning"
    assert case["attempts"][0]["explanation_required_after_estimate"] is True
    assert "4*3 = 12 apples" in case["messages"][-1]["content"]
    assert case["calculation_tutoring"]["final_delivery"]["response"]["content"] == "17"


@pytest.mark.parametrize("unstable_role", [
    "student", "mismatched_first_model", "student_step", "verified_answer", "verified_answer_retry"])
def test_unverifiable_solver_identity_holds_this_case_immediately(apple_problem, unstable_role):
    from lib.contexts.workflows.application.generate_guided_answer import GenerateGuidedAnswer

    roles = []

    def request(role, index, messages, schema):
        roles.append(role)
        if role == "student_step":
            content = json.dumps("4*3;12+5")
        elif role == "student" or role == "verified_answer" and unstable_role == "verified_answer_retry":
            content = "16"
        else:
            content = "17"
        reply = {"content": content, "finish_reason": "stop", "reasoning_content": "", "model": "solver"}
        if role == unstable_role or unstable_role == "mismatched_first_model" and role == "student":
            if unstable_role == "student":
                del reply["model"]
            else:
                reply["model"] = "unexpected-solver"
        return reply

    receipt = apple_problem["verified_calculation_plan"]["receipt_id"]
    case = GenerateGuidedAnswer(request).run(apple_problem, {"version": "test"}, 2, protocol_version="4.10.0",
                                             trusted_solution_receipts={receipt}, expected_first_model="solver")
    assert roles[-1] == ("student" if unstable_role == "mismatched_first_model" else unstable_role)
    assert case["status"] == "awaiting_guidance" and case["continuation"]["reason"] == "solver_identity_unverified"
    assert case["verification"]["answer_key_check"] is None and "training_route" not in case
