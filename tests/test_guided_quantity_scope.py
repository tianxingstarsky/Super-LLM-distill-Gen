import json

import pytest

from lib.contexts.workflows.api import GenerateGuidedAnswer
from lib.contexts.workflows.application.guided_experience import observed_attempt
from lib.contexts.workflows.domain.guided_answers import validate_source
from lib.contexts.workflows.domain.guided_quantity_scope import storage_quantity_findings


QUESTION = ("Pete's memory card can hold 3,000 pictures of 8 megabytes each. "
            "How many pictures can it hold of 6 megabytes each?")
# Two consecutive sentences from the frozen gsm8k train 5613 student receipt.
STUDENT = ('The original capacity calculation ($3,000$ pictures × $8$ MB each) results in $24,000$ MB. '
           'This effectively means "24,000 pages of paper" or "24,000 pixels" (if 1 pixel = 1 byte).')


def test_correct_final_value_does_not_authenticate_a_false_unit_conversion():
    source = validate_source({"id": "photo-storage", "question": QUESTION, "gold": {"kind": "number", "value": "4000"}})
    student = STUDENT + "\nThe card fits 24,000 / 6 = 4,000 images.\nFinal answer: 4000"
    roles = []

    def request(role, index, messages, schema):
        roles.append(role)
        if role == "student":
            return {"model": "student", "content": student, "reasoning_content": "", "finish_reason": "stop"}
        assert role == "coach"
        by_segment = json.loads(messages[-1]["content"])["program_check"]["quantity_findings"]
        page, pixel = by_segment["a1.s2"]
        assert page["status"] == "insufficient_evidence" and page["missing_relation"] == "bytes per page"
        assert pixel["status"] == "contradicted" and pixel["implied_bytes"] == "24000"
        assert pixel["storage_bytes"] == "24000000000" and pixel["conversion"] == "1 pixel = 1 byte"
        assert student[slice(*pixel["span"])] == '"24,000 pixels"'
        return {"model": "coach", "content": json.dumps({"decision": "supported", "diagnosis": "The final value matches.",
                "reusable": [], "independent_approaches": []}), "reasoning_content": "", "finish_reason": "stop"}

    case = GenerateGuidedAnswer(request).run(source, {"version": "test"}, 1, protocol_version="4.9.1")
    assert roles == ["student", "coach"]
    assert case["status"] != "accepted" and case["draft_versions"][0]["review_conflict"] == "quantity_contradiction"
    assert not case["attempts"][0]["verified_stage"]["complete_student_solution"]
    assert observed_attempt(case, 1)["review_observation"]["verdict"]["keep"] is False


@pytest.mark.parametrize(("question", "text"), [
    (QUESTION, "24,000 MB divided by 6 MB per image gives 4,000 images. Pixel counts vary."),
    (QUESTION, "The student wrote: “24,000 MB = 24,000 pixels”."),
    (QUESTION, "24,000 MB equals 24,000 pixels. This is false."),
    (QUESTION, "Is this true: 24 MB = 24 pixels?"),
    (QUESTION, "The previous answer was wrong:\n24 MB = 24 pixels."),
    (QUESTION, "24,000 MB does not mean 24,000 pixels."),
    (QUESTION, "If 24,000 MB equals 24,000 pixels, each pixel would occupy one MB."),
    (QUESTION, "1 MB equals 1,000,000 pixels (if 1 pixel = 1 byte)."),
    (QUESTION, "1 MB equals 1,048,576 pixels (if 1 pixel = 1 byte)."),
    (QUESTION, "Each page occupies 1 MB. 24,000 MB equals 24,000 pages."),
    (QUESTION, "24,000 MB equals 24,000 pages of 1 MB each."),
    (QUESTION, "24 MB equals 24 pages. Each page occupies 1 MB."),
    ("Each page stores 1 MB.", "3 × 8 MB = 24 pages."),
    ("Each page stores 2 MB.", "24,000 MB corresponds to 12,000 pages."),
    ("A remark incorrectly suggests 1 byte per pixel.", "1 MB equals 250,000 pixels."),
    ("There are 1 or 2 bytes per pixel, depending on compression.", "1 MB equals 500,000 pixels."),
    ("A sheet has 12 pages on it.", "24 MB = 24 pages per sheet."),
    ("Images use 4 bits per pixel.", "1 MB = 2,000,000 pixels."),
])
def test_valid_qualified_or_uninterpreted_quantity_remains_with_reviewer(question, text):
    assert storage_quantity_findings(text, question=question) == []


def test_authoritative_page_size_and_exact_source_quote_are_distinguished():
    text = "24 MB = 24 pages."
    found, = storage_quantity_findings(text, question="Each page stores 2 MB.")
    assert found["conversion_source"] == "question" and found["implied_bytes"] == "48000000"
    assert found["storage_bytes"] == "24000000" and found["status"] == "contradicted"
    assert storage_quantity_findings(text, question="Each page stores 2 MB.", quoted_spans=[(0, len(text))]) == []


@pytest.mark.parametrize(("extra", "issue"), [
    ("24,000 MB equals 24,000 pages.", "formal_answer_quantity_conversion_unverified"),
    (STUDENT, "formal_answer_quantity_contradiction"),
])
def test_newly_introduced_storage_conversion_in_formal_delivery(extra, issue):
    source = validate_source({"id": "photos", "question": QUESTION, "gold": {"kind": "number", "value": "4000"}})
    calculation = "3,000 pictures of 8 MB give 24,000 MB. Dividing by 6 MB per picture gives 4,000 pictures."

    def request(role, index, messages, schema):
        if role == "coach":
            text = json.dumps({"decision": "supported", "diagnosis": "The two operations match the problem.",
                               "reusable": [], "independent_approaches": []})
        else:
            assert role in ("student", "final_answer")
            text = calculation + (" " + extra if role == "final_answer" else "") + "\nFinal answer: 4000"
        return {"model": "solver", "content": text, "reasoning_content": "", "finish_reason": "stop"}

    case = GenerateGuidedAnswer(request).run(source, {"version": "test"}, 1, protocol_version="4.9.1")
    check = case["attempts"][0]["formal_answer"]["check"]
    assert case["status"] != "accepted" and check["answer_matches"] is True
    assert check["issue"] == issue
