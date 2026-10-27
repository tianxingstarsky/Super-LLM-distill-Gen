"""Protocol recovery cannot create evidence or remove a quality obligation."""
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from lib.contexts.workflows.domain.authored_trace_transport import (
    authored_response_schema, prepare_authored_response,
)
from lib.contexts.workflows.application.generate_cot import GenerateCot
from lib.domain.authored_reasoning import authored_reasoning_issue
from test_authored_reasoning import derived_candidate
from test_generation_node_use_cases import MemoryGenerationPort, conversation, source


def test_basis_transport_is_lossless_idempotent_and_never_a_semantic_pass():
    raw = derived_candidate()
    raw["reasoning_trace"]["steps"][0]["basis"] = [" 2x+3=13。\n", "两侧允许减去相同实数。"]
    before = deepcopy(raw)
    canonical, receipt = prepare_authored_response(raw, answer_required=True)
    assert raw == before
    assert canonical["reasoning"] == raw["reasoning"] and canonical["answer"] == raw["answer"]
    assert canonical["reasoning_trace"]["steps"][0]["basis"] == " 2x+3=13。\n\n两侧允许减去相同实数。"
    assert receipt["status"] == "normalized" and receipt["semantic_status"] == "not_assessed"
    assert not receipt["errors"] and receipt["content_issue"] is None
    assert receipt["changes"][0]["original"] == raw["reasoning_trace"]["steps"][0]["basis"]
    assert authored_reasoning_issue(canonical) is None
    again, repeated = prepare_authored_response(canonical, answer_required=True)
    assert again == canonical and repeated["status"] == "valid" and not repeated["changes"]
    Draft202012Validator.check_schema(authored_response_schema(answer_required=True))


@pytest.mark.parametrize("basis", [[], [""], ["   "], [False], [1], [None], {"claim": "2x=10"}])
def test_missing_or_wrong_basis_is_not_guessed(basis):
    raw = derived_candidate()
    raw["reasoning_trace"]["steps"][0]["basis"] = basis
    canonical, receipt = prepare_authored_response(raw, answer_required=True)
    assert receipt["status"] == "invalid" and not receipt["changes"]
    assert receipt["errors"][0]["path"] == ["reasoning_trace", "steps", 0, "basis"]
    assert canonical == raw


@pytest.mark.parametrize("basis,issue", [
    (["2x+3=13。", "分析"], "authored_reasoning_vacuous_step"),
    (["2x+3=13。", "2x=10。"], "authored_reasoning_circular_support"),
    (["2x+3=13。", "2x+3=13。"], "authored_reasoning_repeated_basis"),
])
def test_list_cannot_hide_vacuity_self_support_or_repeated_evidence(basis, issue):
    raw = derived_candidate()
    raw["reasoning_trace"]["steps"][0]["basis"] = basis
    _, receipt = prepare_authored_response(raw, answer_required=True)
    assert receipt["content_issue"] == issue


@pytest.mark.parametrize("native", [False, True])
def test_schema_failure_preserves_all_text_and_stops_whole_draft_regeneration(native):
    raw = derived_candidate()
    if native:
        raw.pop("answer")
    del raw["reasoning_trace"]["steps"][0]["inference"]
    port = MemoryGenerationPort([raw])  # A second generation is an error.
    port.validate_native_prefill = lambda: None
    usecase = GenerateCot(port, {"authored_trace_transport_version": 1,
        "node_generation": {"cot": {"answer_mode": "native_prefill" if native else "joint"}}})
    port.generate_native_answer = usecase.generate_native_answer
    row = usecase.generate_styled(conversation(), {"instruction": "完整论证"})[0]
    assert row["status"] == "quarantined" and row["reason"] == "cot_response_protocol_invalid"
    assert row["reasoning"] == [raw["reasoning"]]
    assert row["answer"] == raw.get("answer", "")
    assert row["feedback"]["raw_response"] == raw
    assert row["feedback"]["response_transport"]["errors"][0]["rule"] == "required"
    assert len(port.calls) == 1


def test_normalized_native_prefix_still_needs_review_and_preserves_prefill_bytes():
    raw = derived_candidate()
    raw.pop("answer")
    raw["reasoning_trace"]["steps"][0]["basis"] = ["2x+3=13。"]
    port = MemoryGenerationPort([raw])
    checks, answers = [], []
    def reject_prefix(*args, **options):
        checks.append(deepcopy(args))
        return {"keep": False, "adherence": 2, "reason": "A concrete unresolved claim."}
    port.check_adherence = reject_prefix
    port.request_native_answer = lambda *args, **kwargs: answers.append(args)
    usecase = GenerateCot(port, {"authored_trace_transport_version": 1})
    value, failure = usecase.generate_native_answer(source(), {
        "prompt": conversation()["messages"][:-1], "source": source()}, {"instruction": "完整论证"}, None, 0)
    assert value is None and failure["prefix_review"] == "A concrete unresolved claim."
    assert len(checks) == 1 and answers == []
    assert checks[0][2]["reasoning"] == raw["reasoning"]
    assert checks[0][2]["reasoning_trace"]["steps"][0]["basis"] == "2x+3=13。"


def test_joint_normalization_does_not_override_negative_quality_review():
    raw = derived_candidate()
    raw["reasoning_trace"]["steps"][0]["basis"] = ["2x+3=13。"]
    port = MemoryGenerationPort([raw, raw])
    calls = []
    def assess(*args, **options):
        calls.append(args)
        return {"keep": False, "grounded": False, "reasoning_valid": False, "correctness": 1,
                "scores": {k: 1 for k in ("correctness", "reasoning", "grounding", "instruction", "safety")},
                "reason": "Actual incorrect claim, not a transport issue."}
    port.assess = assess
    row = GenerateCot(port, {"authored_trace_transport_version": 1,
        "authored_reasoning_protocol_version": 1}).generate_styled(conversation(), {"instruction": "完整论证"})[0]
    assert row["status"] == "quarantined" and len(calls) == 2
    assert row["judge"]["correctness"] == 1
