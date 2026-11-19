import json

import pytest

from lib.contexts.workflows.api import GenerateGuidedAnswer
from lib.contexts.workflows.domain.guided_answers import validate_source
from lib.contexts.workflows.domain.guided_teacher_delivery import inspect_solver_delivery


def response(content, model):
    return {"content": content if isinstance(content, str) else json.dumps(content),
            "reasoning_content": "", "finish_reason": "stop", "model": model}


# The paused beaker example distinguishes the tested subset from the laboratory inventory.
BEAKER_QUESTION = (
    "A laboratory has 22 beakers. Each test uses three drops of solution. "
    "After 45 drops, all 8 copper-positive beakers have been found. "
    "How many tested beakers were negative? Show why the result follows."
)
BEAKER_TRACE = "The 45 drops cover 45 / 3 = 15 tests.\nThe tested subset has 15 - 8 = 7 negative beakers."
BEAKER_FINAL = "45 drops at 3 drops per test cover 15 beakers. Subtract the 8 positives: 15 − 8 = 7.\nFinal answer: 7"
PRIVATE_REFERENCE = "Reference calculation from the answer manual: 45/3 - 8 = 7."
PRIVATE_REVIEW = "The reviewer independently checked the tested subset."


@pytest.fixture
def reviewed_beakers():
    def run(final_text, final_model="solver-08b", *, answer_requirement="worked_solution"):
        source = validate_source({
            "id": "beakers", "question": BEAKER_QUESTION, "gold": {"kind": "number", "value": "7"},
            "task_system": "Use the stated test conditions.", "answer_requirement": answer_requirement,
            "source_reasoning": PRIVATE_REFERENCE,
        })
        calls = []

        def request(role, index, messages, schema):
            calls.append((role, messages))
            if role == "student":
                return response(BEAKER_TRACE + "\nFinal answer: 7", "solver-08b")
            if role == "coach":
                return response({"decision": "supported", "diagnosis": PRIVATE_REVIEW, "reusable": [],
                                 "independent_approaches": []}, "tutor-9b")
            if role == "disclosure_draft":
                return response({"decision": "insufficient", "bridge": "", "hint": "", "question_quote": "",
                                 "reason": "No additional premise supports a new hint."}, "tutor-9b")
            assert role == "final_answer" and schema is None
            return response(final_text, final_model)

        case = GenerateGuidedAnswer(request).run(source, {"version": "test"}, 1, protocol_version="4.9.0")
        return case, calls
    return run


def test_worked_answer_is_the_reviewed_solvers_own_continuation(reviewed_beakers):
    # This imported dataset default conflicts with the explicit request to show the reasoning.
    case, calls = reviewed_beakers(BEAKER_FINAL, answer_requirement="final_only")
    assert [role for role, _ in calls] == ["student", "coach", "final_answer"]
    messages = calls[-1][1]
    assert messages[-1] == {"role": "assistant", "reasoning_content": BEAKER_TRACE, "content": ""}
    assert [m["content"] for m in messages if m["role"] == "user"] == [BEAKER_QUESTION]
    assert "Use the stated test conditions." in messages[0]["content"]
    for private in (PRIVATE_REFERENCE, PRIVATE_REVIEW, "reviewed_student_steps"):
        assert private not in json.dumps(messages)

    assert case["status"] == "accepted"
    assert case["messages"][-1] == {"role": "assistant", "reasoning_content": BEAKER_TRACE, "content": BEAKER_FINAL}
    delivered = case["attempts"][-1]["formal_answer"]
    assert delivered["response"] == response(BEAKER_FINAL, "solver-08b")
    assert delivered["check"]["answer_matches"] is True
    provenance = case["final_answer_provenance"]
    assert (provenance["author"], provenance["model"], provenance["receipt"]) == (
        "student", "solver-08b", "001-final_answer.json")


@pytest.mark.parametrize("text,model", [
    ("", "solver-08b"),
    ("7", "solver-08b"),
    ("The 22 beakers include 8 positives, so 22 − 8 = 14.\nFinal answer: 14", "solver-08b"),
    (BEAKER_FINAL, "tutor-9b"),
], ids=["empty", "bare_conclusion", "wrong_answer", "different_author"])
def test_invalid_or_misattributed_formal_delivery_cannot_be_accepted(reviewed_beakers, text, model):
    case, _ = reviewed_beakers(text, model)
    assert case["status"] != "accepted"
    final = case["attempts"][-1]["formal_answer"]
    assert final["response"] == response(text, model)
    assert final["check"]["issue"]
    if model != "solver-08b":
        assert final["check"]["issue"] == "formal_answer_model_unverified"


def test_direct_correct_answer_needs_only_the_original_student_call():
    source = validate_source({"id": "jobs", "question": "There are six jobs. Return only the number of jobs.",
                              "gold": {"kind": "number", "value": "6"}, "answer_requirement": "final_only"})
    calls = []

    def request(role, index, messages, schema):
        calls.append(role)
        assert role == "student"
        return response("6", "solver-08b")

    case = GenerateGuidedAnswer(request).run(source, {"version": "test"}, 1, protocol_version="4.9.0")
    assert calls == ["student"]
    assert case["status"] == "accepted" and case["training_route"] == "nonthinking_direct"
    assert case["messages"][-1] == {"role": "assistant", "content": "6"}


@pytest.mark.parametrize("text,form,accepted", [
    ("Subtract the 8 elapsed minutes from 15: 15 − 8 = 7.\n7 minutes.", "worked_solution", True),
    ('Mira wrote "the answer is 9", but that overlooks the full 8 minutes elapsed. '
     'Subtracting all 8 from 15 gives 7.\nFinal answer: 7', "worked_solution", True),
    ("No idea.\n7", "worked_solution", False),
    ("There are 7 minutes left.\nFinal answer: 7", "final_only", False),
], ids=["unit_with_period", "quoted_wrong_answer", "unsupported_conclusion", "extra_sentence_in_numeric_output"])
def test_solver_checks_the_requested_public_delivery(text, form, accepted):
    task = {"id": "time", "question": "A 15-minute timer started 8 minutes ago. How many minutes remain?",
            "gold": {"kind": "number", "value": "7"}, "answer_requirement": form}
    if form == "final_only":
        task.update(task_system="Return only the number.", direct_answer_syntax="number")
    else:
        task["question"] += " Show your work and give the answer in minutes."
    source = validate_source(task)
    check = inspect_solver_delivery(response(text, "solver-08b"), source,
                                    {"a1.s1": "15 minus 8 is 7 minutes."}, form)
    assert check["format_ok"] is accepted
    if accepted:
        assert check["answer_matches"] is True and check["answer"] == "7"


def test_tutored_solver_continuation_carries_only_the_public_solving_history():
    source = validate_source({"id": "beakers", "question": BEAKER_QUESTION, "gold": {"kind": "number", "value": "7"},
                              "source_reasoning": PRIVATE_REFERENCE, "answer_requirement": "worked_solution"})
    premise = "Each test uses three drops of solution"
    hint = "Divide the total number of drops by the drops per test to count the beakers actually tested."
    mistaken = "All 22 beakers were tested, so subtract the eight positive ones."
    calls = []

    def request(role, index, messages, schema):
        calls.append((role, index, messages))
        if role == "student":
            text = mistaken + "\nFinal answer: 14" if index == 1 else BEAKER_TRACE + "\nFinal answer: 7"
            return response(text, "solver-08b")
        if role == "final_answer":
            return response(BEAKER_FINAL, "solver-08b")
        if role == "disclosure_draft":
            value = {"decision": "offer", "question_quote": premise, "bridge": premise + ".", "hint": hint,
                     "reason": "The inventory count differs from the number of actual tests."}
        elif role == "disclosure":
            proposal = json.loads(messages[-1]["content"])["proposed_next_teacher_disclosure"]
            value = {"decision": "release", "citations": [{"evidence_id": "question", "quote": premise}],
                     "bridge": proposal["bridge"], "reason": "The drops per test establish the ratio.",
                     "pairing": {"student_fit": True, "evidence_support": True, "history_fit": "first"}}
        else:
            assert role == "coach"
            value = {"decision": "supported" if index == 2 else "revise:a1.s1",
                     "diagnosis": PRIVATE_REVIEW, "reusable": [], "independent_approaches": []}
        return response(value, "tutor-9b")

    case = GenerateGuidedAnswer(request).run(source, {"version": "test"}, 2, protocol_version="4.9.0")
    assert case["status"] == "accepted" and case["training_route"] == "guided_reasoning"
    assert [role for role, _, _ in calls] == [
        "student", "coach", "disclosure_draft", "disclosure", "student", "coach", "final_answer"]
    final_prefix = calls[-1][2][-1]["reasoning_content"]
    assert final_prefix.index(mistaken) < final_prefix.index(hint) < final_prefix.index(BEAKER_TRACE)
    assert PRIVATE_REVIEW not in final_prefix and PRIVATE_REFERENCE not in final_prefix
    assert case["messages"][-1] == {"role": "assistant", "content": BEAKER_FINAL, "reasoning_content": final_prefix}
    assert case["final_answer_provenance"]["receipt"] == "002-final_answer.json"
