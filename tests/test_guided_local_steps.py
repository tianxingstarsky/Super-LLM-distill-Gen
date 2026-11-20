import json

import pytest

from lib.contexts.workflows.application.generate_guided_answer import GenerateGuidedAnswer
from lib.contexts.workflows.domain.guided_answers import validate_source
from lib.contexts.workflows.domain.guided_local_steps import check_local_step, local_step_target
from lib.contexts.workflows.domain.guided_teaching_steps import verified_steps_digest
from lib.contexts.workflows.infrastructure.guided_answer_records import _check_rendered_guidance


def signed_source():
    fixed = "The bit at position 2, formerly fixed to 1, is changed to 0"
    pair = "The bits at positions 3 and 4 must be different"
    question = f"Consider a four-bit string. {fixed}. {pair}. How many strings qualify? Give the integer only."
    row = {"id": "four-bits", "question": question, "gold": {"kind": "number", "value": "4"},
           "answer_requirement": "final_only"}
    steps = []
    for ident, quote, op in [
            ("fixed-position", fixed, "Mark the changed position and keep its old value separate from the value now in force."),
            ("permitted-pair", pair, "Draw two slots for the linked positions. List only pairs satisfying the stated equality or inequality; leave the other positions blank.")]:
        start = question.index(quote)
        steps.append(dict(id=ident, kind="operation", quote=quote, span=[start, start+len(quote)],
                          operation=op, verification="Checked against the stated constraint."))
    receipt = verified_steps_digest(row["id"], question, row["gold"], steps)
    row["verified_teaching_steps"] = {"receipt_id": receipt, "steps": steps}
    return validate_source(row, allow_verified_teaching_steps=True, trusted_step_receipts={receipt}), receipt


@pytest.mark.parametrize("first_step,status", [
    ('I read the changed slot in the question.\n["2","1","0"]', "verified_local_value"),
    ('["2","0","1"]', "local_value_mismatch"),
    ('{"blocked":"Which bit remains fixed?"}', "blocked"),
    ('Final answer: 4', "unverified"),
])
def test_local_reports_and_obstacles_flow_to_the_next_tutor_and_whole_answer(first_step, status):
    source, receipt = signed_source()
    steps = source["verified_teaching_steps"]["steps"]
    inputs = {}
    hints = ["Separate the replacement slot from its former setting.", "Try each value of the first linked slot."]
    solved = "The first bit has two choices and the final pair has two assignments.\nFinal answer: 4"

    def request(role, index, messages, schema):
        inputs[role, index] = messages
        if role == "student":
            reply = {1: "8", 2: first_step, 3: '["10","01"]', 4: solved}[index]
        elif role == "disclosure_draft":
            step = steps[index-1]
            if index == 2:
                packet = json.loads(messages[-1]["content"])
                assert packet["current_difficulty"]["local_step"]["status"] == status
            reply = dict(decision="apply:"+step["id"], question_quote=step["quote"], bridge=step["quote"]+".",
                         hint=hints[index-1], reason="Use the selected constraint.")
        elif role == "disclosure":
            step = steps[index-1]
            citations = [dict(evidence_id="question", quote=step["quote"])]
            if index > 1:
                citations.append(dict(evidence_id="disclosed.1", quote=hints[0]))
            packet = json.loads(messages[-1]["content"])["proposed_next_teacher_disclosure"]
            reply = dict(decision="release", bridge=packet["bridge"], citations=citations, reason="The hint applies.",
                         pairing=dict(evidence_support=True, student_fit=True, history_fit="first" if index == 1 else "continuous"))
        elif role == "coach":
            assert index == 4
            reply = dict(decision="supported", diagnosis="Both free choices were counted.", reusable=[], independent_approaches=[])
        else:
            assert role == "final_answer" and index == 4
            reply = "4"
        return dict(model="learner" if role in {"student","final_answer"} else "reviewer",
                    content=reply if isinstance(reply, str) else json.dumps(reply), finish_reason="stop")

    case = GenerateGuidedAnswer(request).run(source, {"version":"test"}, 3, protocol_version="4.10.3",
                                            until_correct=True, expected_first_model="learner", trusted_solution_receipts={receipt})
    assert case["status"] == "accepted" and case["verification"]["answer_key_check"]
    assert [a["local_step"]["status"] for a in case["attempts"][1:3]] == [status,"verified_local_value"]
    assert case["attempts"][1]["student"]["content"] == first_step
    for n in (2,3):
        attempt = case["attempts"][n-1]
        assert attempt["check"]["answer_matches"] is None and not attempt["verified_stage"]["complete_student_solution"]
        assert "Final answer:" not in inputs["student",n][0]["content"]
        prior = case["attempts"][n-2]
        _check_rendered_guidance(case, prior, {"messages":inputs["student",n]}, [h["text"] for h in case["disclosures"][:n-2]], prior["disclosure"])
    assert "Final answer:" in inputs["student",4][0]["content"]
    assert '["10","01"]' in inputs["student",4][-1]["content"]
    fields = case["shared_student_findings"]
    assert len(fields) == 1 + (status == "verified_local_value")
    for item in fields:
        raw = case["attempts"][item["source_attempt"]-1]["student"]["content"]
        assert raw[slice(*item["source_span"])] == item["text"]
    assert "continuation_provenance" not in case


def test_local_operation_needs_the_bound_source_and_a_matching_verifier():
    source, receipt = signed_source()
    hint = {"source_step_receipt":receipt, "source_step_id":"permitted-pair", "text":"List the pairs."}
    target = local_step_target(source, hint, {receipt}, chinese=False)
    for report in ('["00","11"]', '["01","10","10"]', '["01","10"]\nI am unsure.'):
        response = {"content":report, "finish_reason":"stop"}
        action, finding = check_local_step(target, response, 2)
        assert not finding and action["status"] != "verified_local_value"
    assert local_step_target(source, hint, set(), chinese=False) is None
    assert local_step_target({**source,"question":"another task"}, hint, {receipt}, chinese=False) is None
    assert local_step_target(source, {**hint,"source_step_id":"another-target"}, {receipt}, chinese=False) is None


def test_portfolio_substep_checks_the_selected_projects_hours_and_updated_cap():
    quote = "The hours limit falls from 20 to 16"
    question = ("The projects have (cost, hours, benefit) triples E=(4, 3, 19), G=(2, 8, 15). "
                "In E→G, choosing the left project requires choosing the right. " + quote + ".")
    op = ("As a trial, take the projects E and G from the previous dependency. Count each project's hours once "
          "and compare the sum with the new cap. This checks this resource alone.")
    gold = {"kind":"exact", "value":"EG"}

    def target_for(text, operation):
        start = text.index(quote)
        steps = [dict(id="updated-hours", kind="operation", operation=operation, quote=quote,
                      span=[start,start+len(quote)], verification="The named pair was checked against the cap.")]
        receipt = verified_steps_digest("trial", text, gold, steps)
        source = dict(id="trial", question=text, gold=gold, verified_teaching_steps={"receipt_id":receipt,"steps":steps})
        hint = {"text":operation, "source_step_id":"updated-hours", "source_step_receipt":receipt}
        return local_step_target(source, hint, {receipt}, chinese=False)

    target = target_for(question, op)
    for content, status in [('["11","16","<"]', "verified_local_value"), ('["6","16","<"]', "local_value_mismatch"),
                            ('["11","20","<"]', "local_value_mismatch"), ('["11","16",">"]', "local_value_mismatch")]:
        action, _ = check_local_step(target, {"content":content,"finish_reason":"stop"}, 2)
        assert action["status"] == status
    assert target_for(question.replace("(cost, hours, benefit)", "(hours, cost, benefit)"), op) is None
    assert target_for(question, op.replace("E and G", "E and H")) is None
    assert target_for(question, "Now optimize the whole portfolio.") is None
