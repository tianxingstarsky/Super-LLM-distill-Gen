import json

import pytest

from lib.contexts.workflows.api import GenerateGuidedAnswer
from lib.contexts.workflows.application.guided_experience import observed_attempt, observed_teacher_disclosure
from lib.contexts.workflows.application.review_guided_disclosure import review_or_recommend_disclosure
from lib.contexts.workflows.domain.guided_answers import validate_source
from lib.contexts.workflows.domain.guided_student_calculations import asserted_arithmetic_errors


def reply(text, model):
    return {"model": model, "content": text if isinstance(text, str) else json.dumps(text),
            "finish_reason": "stop", "reasoning_content": ""}


def test_shopping_check_refutes_teacher_approval_despite_the_correct_final_value():
    # gsm8k train 4954, original verification sentence from a wrongly approved student response.
    question = ("Jangshe spent $610 on 7 pieces of clothing. One piece was $49 and another piece was $81. "
                "If the other pieces were all the same price, how many dollars was one of the other pieces?")
    equation = "(49 + 81) + (96 + 96) + (96 + 96) = 130 + 420 + 576 = 610"
    student = ("The cost of one of the other pieces was $96. The verification involves confirming that all seven "
               f"items sum to $610: ${equation}$, which matches the spending amount.\nFinal answer: 96")
    source = validate_source({"id": "shopping", "question": question, "gold": {"kind": "number", "value": "96"}})
    roles = []

    def request(role, index, messages, schema):
        roles.append(role)
        if role == "student":
            return reply(student, "solver")
        assert role == "coach"
        packet = json.loads(messages[-1]["content"])
        error, = packet["program_check"]["arithmetic_errors"]["a1.s2"]
        assert student[slice(*error["span"])] == equation
        assert error["values"] == ["514", "1126", "610"]
        return reply({"decision": "supported", "diagnosis": "The final value agrees.", "reusable": [],
                      "independent_approaches": []}, "coach")

    case = GenerateGuidedAnswer(request).run(source, {"version": "test"}, 1, protocol_version="4.9.1")
    assert roles == ["student", "coach"] and case["status"] != "accepted"
    first = case["attempts"][0]
    assert first["check"]["answer_matches"] is True and first["coach"]["verdict"] == "supported"
    assert first["verified_stage"]["complete_student_solution"] is False
    assert observed_attempt(case, 1)["review_observation"]["verdict"]["keep"] is False


@pytest.mark.parametrize("text", [
    "The question says “45 / 3 = 16”. Check this assertion.",
    "The claim 45 / 3 = 16 is false. Use the number of tested beakers.",
    "If 45 / 3 = 16, what follows?",
    "Counterexample:\n45 / 3 = 16.",
    "In binary, 1 + 1 = 10.",
    "This is rounded: 5 / 3 = 1.67.",
    "For the inventory, 22 - 8 = 14. Now distinguish the tested subset.",
    "x(45 / 3) = 16.",
    "3 x 10 + 2 = 32.",
    "学生写成45 / 3 = 16个，但这与题设不符。",
])
def test_unsupported_or_qualified_claim_is_not_a_proved_arithmetic_error(text):
    assert asserted_arithmetic_errors(text) == []


def test_chinese_arithmetic_next_to_a_quantity_unit_has_exact_offsets():
    text = "共测试了45 / 3 = 16个烧杯。"
    error, = asserted_arithmetic_errors(text)
    assert text[slice(*error["span"])] == "45 / 3 = 16" and error["values"] == ["15", "16"]


# The local certified stamp run, second private tutor submission. The numerical
# key 3500 is correct even though the teacher's own explanation computes 2270.
STAMP_DIAGNOSIS = ("The correct final answer is 3500 (270 + 40×50), which the student's hint "
                   "and reasoning failed to derive from evidence.")


@pytest.mark.parametrize("text", [
    "The correct final answer is 3500 (1500 + 40×50).",
    "The student says the correct final answer is 3500 (270 + 40×50).",
    "The correct final answer is 3500 (270 stamps + 40 pages).",
    "The correct final answer is 3500 (270 + 40×50) mm.",
    "The correct final answer is 3500 (2020-2021).",
    "The correct final answer is 3500 (270 + 40×50). That is wrong.",
    "Consider the counterexample: The correct final answer is 3500 (270 + 40×50).",
    "The correct final answer is 3500 (the stamp total, excluding duplicates).",
    "The student's calculation was 2 + 2 = 5.",
])
def test_private_diagnosis_only_refutes_its_own_asserted_arithmetic(text):
    assert asserted_arithmetic_errors(text, check_result_expressions=True) == []


@pytest.mark.parametrize("final,correct", [("3500", True), ("1170", False)])
def test_refuted_private_judgment_preserves_checked_student_work(final, correct):
    question = ("Stella collects stamps. She counted 50 pages in her collector's album. There are 5 rows "
                "of 30 stamps in each of the first 10 pages of her album. The rest of the pages each have "
                "50 stamps. How many stamps are in Stella's album?")
    source = validate_source({"id": "stamps", "question": question, "source_reasoning":
        "There are 30 x 5 = <<30*5=150>>150 stamps on each of the first 10 pages.",
        "gold": {"kind": "number", "value": "3500"}})
    raw = {"decision": "supported", "diagnosis": STAMP_DIAGNOSIS,
           "reusable": [{"segment_id": "a1.s2", "basis_id": "question", "depends_on": []}],
           "independent_approaches": []}
    student = f"30 * 5 = 150 stamps per initial page. I estimated the rest.\nFinal answer: {final}"
    def request(role, index, messages, schema):
        if role == "student":
            return reply(student, "student")
        assert role == "coach"
        return reply(raw, "tutor")

    case = GenerateGuidedAnswer(request).run(source, {"version":"test"}, 1, protocol_version="4.9.1")
    attempt = case["attempts"][0]
    error, = attempt["coach_feedback_check"]["arithmetic_errors"]
    assert STAMP_DIAGNOSIS[slice(*error["span"])] == "3500 (270 + 40×50)"
    assert error["values"] == ["3500", "2270"] and error["expression"] == "270 + 40×50"
    assert json.loads(attempt["coach_response"]["content"]) == raw
    assert attempt["coach"]["verdict"] == "uncertain" and attempt["coach"]["reusable"] == []
    assert not attempt["verified_stage"]["complete_student_solution"] and case["status"] != "accepted"
    assert any(f["evidence_level"] == "source_verified_student_calculation"
               and "150" in f["text"] for f in case["shared_student_findings"])
    if correct:
        with pytest.raises(ValueError, match="alone does not establish"):
            observed_attempt(case, 1)
    else:
        # A new extraction from an older archive also excludes the refuted explanation.
        attempt.pop("coach_feedback_check")
        attempt["coach"]["diagnosis"] = STAMP_DIAGNOSIS
        observation = observed_attempt(case, 1)["review_observation"]["verdict"]
        assert observation["keep"] is False and STAMP_DIAGNOSIS not in observation["reason"]


def test_beaker_hint_rewrite_hides_the_wrong_calculation_and_does_not_supply_the_key():
    premise = "If the beakers are tested one by one for copper ions and 45 drops are used"
    question = ("A laboratory has 22 beakers of liquid in a fume hood, and 8 of the beakers have copper ions in them. "
                "Adding three drops of a solution will turn the liquid in a beaker blue if there are copper ions present. "
                + premise + " before all 8 beakers with copper ions are found, how many beakers without copper ions were tested?")
    private = "Source computation: 45 / 3 = 15 tested, then 15 - 8 = 7 negatives."
    source = validate_source({"id": "tested", "question": question, "source_reasoning": private,
                              "answer_requirement": "final_only", "gold": {"kind": "number", "value": "7"}})
    roles = []
    safe_hint = "Divide the number of drops used by the amount used in one test."
    wrong_hint = "45 / 3 = 16 tests."

    def request(role, index, messages, schema):
        roles.append(role)
        if role == "student" and index == 1:
            return reply("14", "solver")
        if role == "student":
            assert safe_hint in messages[-1]["content"]
            assert all(word not in json.dumps(messages) for word in (wrong_hint, private, "15 - 8", "7 negatives"))
            return {"model": "solver", "content": "", "finish_reason": "length", "reasoning_content": ""}
        packet = json.loads(messages[-1]["content"])
        assert packet["private_source_reference"]["attributed_source_reasoning"] == private
        if role == "disclosure_recheck":
            proposed = packet["proposed_next_teacher_disclosure"]
            return reply({"decision": "release", "bridge": proposed["bridge"], "reason": "Tests the stated relation.",
                          "citations": [{"evidence_id": "question", "quote": premise}],
                          "pairing": {"history_fit": "first", "evidence_support": True, "student_fit": True}}, "coach")
        if role == "disclosure_rewrite":
            error, = packet["requested_revision"]["arithmetic_errors"]
            assert error["equation"] == "45 / 3 = 16" and error["values"] == ["15", "16"]
        else:
            assert role == "disclosure_draft"
        return reply({"decision": "offer", "question_quote": premise, "bridge": "Count only the tested beakers.",
                      "hint": safe_hint if role == "disclosure_rewrite" else wrong_hint,
                      "reason": "Separate the tested subset from the inventory."}, "coach")

    case = GenerateGuidedAnswer(request).run(source, {"version": "test"}, 2, protocol_version="4.9.1")
    assert roles == ["student", "disclosure_draft", "disclosure_rewrite", "disclosure_recheck", "student"]
    first, second = case["attempts"][0]["disclosure_work"]
    assert "review_response" not in first and second["validated_citations"]
    assert observed_teacher_disclosure(case, 1, 0)["review_observation"]["verdict"]["keep"] is False
    assert observed_teacher_disclosure(case, 1, 1)["review_observation"]["verdict"]["keep"] is True


def test_even_a_catalogue_nomination_cannot_override_a_numeric_contradiction():
    question = "Each tray holds three containers."
    step = {"id": "trays", "kind": "operation", "operation": "Two trays hold 2 * 3 = 5 containers.",
            "quote": question, "span": [0, len(question)]}
    source = {"question": question, "gold": {"kind": "number", "value": "6"},
              "verified_teaching_steps": {"receipt_id": "checked-source", "steps": [step]}}
    attempt = {"attempt": 1, "check": {"format_ok": True, "answer_matches": False}, "coach": {},
               "student": {"content": "12"}, "recommend_verified_operation": True, "derivation_available": False}
    approved, issue = review_or_recommend_disclosure(
        lambda *args: pytest.fail("A refuted operation reached the model reviewer"),
        source, attempt, [], {}, [], {"kind": "idea", "text": "", "replaces_from": "final_answer"},
        teacher_experience="", tried=set(), check_arithmetic=True)
    assert approved is None and issue == "verified_teaching_operations_exhausted"
    rejected, = attempt["disclosure_work"]
    assert rejected["arithmetic_errors"][0]["values"] == ["6", "5"] and "review_checkpoint" not in rejected
    with pytest.raises(ValueError, match="program nomination"):
        observed_teacher_disclosure({"attempts": [attempt], "question": question}, 1, 0)


def test_solver_cannot_introduce_a_refuted_equation_in_formal_delivery():
    source = validate_source({"id": "trays", "question": "How many cups in two trays of three? Show your work.",
                              "gold": {"kind": "number", "value": "6"}})
    def request(role, index, messages, schema):
        if role == "student":
            return reply("Two trays hold 2 * 3 = 6 cups.\nFinal answer: 6", "solver")
        if role == "coach":
            return reply({"decision": "supported", "diagnosis": "Each tray has three cups.",
                          "reusable": [], "independent_approaches": []}, "coach")
        assert role == "final_answer"
        return reply("Two trays hold 2 * 3 = 5 cups.\nFinal answer: 6", "solver")

    case = GenerateGuidedAnswer(request).run(source, {"version": "test"}, 1, protocol_version="4.9.1")
    assert case["status"] != "accepted"
    check = case["attempts"][0]["formal_answer"]["check"]
    assert check["issue"] == "formal_answer_arithmetic_contradiction" and check["answer_matches"] is True
