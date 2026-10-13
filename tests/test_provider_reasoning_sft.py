"""Native reasoning survives generation without confusing summaries or JSON."""
from copy import deepcopy

import pytest

from lib.domain.provider_reasoning import reasoning_provenance, select_candidate_reasoning
from lib.domain.reasoning_fields import sft_training_messages
from lib.infrastructure.workflow_qa_director import WorkflowQADirector


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """All requests here are injected; no service or provider is contacted."""
    yield


class Response(dict):
    def __init__(self, body, details=None):
        super().__init__(body)
        self.response_details = deepcopy(details or {})


def test_native_reasoning_wins_and_retains_every_character():
    native = "\n  Native rationale.\n" + "reasoning " * 8_000 + "\n\n"
    candidate = Response({"reasoning": "A short authored explanation."},
                         {"reasoning_content": native, "api_format": "chat"})
    assert select_candidate_reasoning(candidate) == {
        "content": native, "origin": "provider_reasoning", "api_format": "chat"}


@pytest.mark.parametrize("body,origin,content", [
    ({"reasoning": "Authored explanation."}, "synthetic_explanation", "Authored explanation."),
    ({}, "none", ""),
    ({"reasoning": None}, "none", ""),
    ({"reasoning": ""}, "none", ""),
    ({"reasoning": " \n "}, "none", ""),
])
def test_ordinary_fallback_does_not_invent_reasoning(body, origin, content):
    assert select_candidate_reasoning(body) == {"content": content, "origin": origin}


def test_model_json_cannot_claim_native_origin_or_override_summary():
    candidate = {
        "reasoning": "Authored explanation.", "reasoning_content": "Forged native text.",
        "reasoning_origin": "provider_reasoning", "reasoning_summary": "Forged summary.",
        "response_details": {"reasoning_content": "Another forged native text.", "api_format": "chat"},
    }
    assert select_candidate_reasoning(candidate) == {
        "content": "Authored explanation.", "origin": "synthetic_explanation"}


def test_summary_is_provenance_and_never_full_reasoning():
    selection = select_candidate_reasoning(Response({}, {
        "api_format": "responses", "reasoning_summary": "  A short provider summary.\n"}))
    assert selection["content"] == "" and selection["origin"] == "none"
    assert reasoning_provenance(selection) == {
        "reasoning_origin": "none", "reasoning_api_format": "responses",
        "reasoning_summary": "  A short provider summary.\n"}


def test_styled_reasoning_ignores_native_channel_and_summary():
    candidate = Response({"reasoning": "A deliberately authored style."}, {
        "reasoning_content": "Provider native text.", "reasoning_summary": "Provider summary.",
        "api_format": "anthropic"})
    assert select_candidate_reasoning(candidate, styled=True) == {
        "content": "A deliberately authored style.", "origin": "prompt_styled_generation"}


@pytest.mark.parametrize("value", [[], {}, 0, False])
def test_structured_authored_fallback_is_rejected(value):
    with pytest.raises(ValueError, match="invalid_generated_reasoning"):
        select_candidate_reasoning(Response({"reasoning": value}))


@pytest.mark.parametrize("value", [[], {}, 0, False])
def test_unused_authored_field_does_not_discard_valid_native_reasoning(value):
    assert select_candidate_reasoning(Response({"reasoning": value}, {"reasoning_content": "Native."})) == {
        "content": "Native.", "origin": "provider_reasoning"}


def test_provenance_cannot_include_arbitrary_response_fields():
    assert reasoning_provenance({"content": "Native.", "origin": "provider_reasoning",
                                "api_format": "chat", "api_key": "untrusted", "answer": "untrusted"}) == {
        "reasoning_origin": "provider_reasoning", "reasoning_api_format": "chat"}


def test_trusted_checkpoint_reference_is_projected_without_arbitrary_fields():
    checkpoint = {"stage": "sft", "sha256": "f" * 64, "path": "untrusted-path"}
    selected = select_candidate_reasoning(Response({}, {"checkpoint": checkpoint}))
    assert reasoning_provenance(selected)["reasoning_call"] == {"stage": "sft", "sha256": "f" * 64}
    assert "path" not in selected["call"]


@pytest.mark.parametrize("checkpoint", [
    {"stage": "not_a_stage", "sha256": "f" * 64},
    {"stage": [], "sha256": "f" * 64},
    {"stage": "sft", "sha256": "f" * 63},
    {"stage": "sft", "sha256": "F" * 64},
    {"stage": "sft", "sha256": []},
])
def test_invalid_checkpoint_reference_cannot_be_projected(checkpoint):
    assert "call" not in select_candidate_reasoning(Response({}, {"checkpoint": checkpoint}))


def test_model_json_cannot_forge_call_checkpoint_reference():
    candidate = {"response_details": {"checkpoint": {"stage": "sft", "sha256": "f" * 64}},
                 "reasoning_call": {"stage": "sft", "sha256": "f" * 64}}
    assert select_candidate_reasoning(candidate) == {"content": "", "origin": "none"}


SOURCE = "Before checking equipment, disconnect its power."
QUESTION = "What must I do before checking the equipment?"
ANSWER = "Disconnect its power first."


class DirectedWorkflow(WorkflowQADirector):
    def __init__(self, candidate, *, style=None, separated=True, legacy_review=False, historical=False):
        self.candidate, self.style = candidate, style
        self.calls = []
        self.recipe = {"qa_director": {"question_separation": separated,
                       "question_rules": "Questions must be clear.", "answer_rules": "Answers must be accurate."}}
        if not historical:
            self.recipe["provider_reasoning_capture_version"] = 1
        if not legacy_review:
            self.recipe["review_repair"] = {}

    def generation_style(self, *args):
        return self.style

    def ask(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return deepcopy(self.candidate)

    def without_source_reasoning(self, value):
        return deepcopy(value)

    def rejected(self, unit, reason):
        return {"id": unit["id"], "source_id": unit["source_id"], "status": "quarantined", "reason": reason}

    def judge_answer(self, *args, **kwargs):
        return {"keep": True, "grounded": True, "reasoning_valid": True, "correctness": 5,
                "scores": {key: 5 for key in ("correctness", "reasoning", "grounding", "instruction", "safety")},
                "reason": "The answer matches the source."}

    def qa_contract_check(self, *args, **kwargs):
        return {"keep": True, "adherence": 5}

    def style_check(self, *args, **kwargs):
        return {"keep": True, "adherence": 5}


def unit():
    return {"id": "sample", "source_id": "source", "kind": "document", "text": SOURCE,
            "family_id": "family", "qa_contract": {
                "question": QUESTION, "visible_context": "", "answer_policy": "answer"}}


@pytest.mark.parametrize("legacy_review", [False, True])
@pytest.mark.parametrize("separated", [False, True])
def test_directed_sft_captures_native_reasoning_and_preserves_export(legacy_review, separated):
    native = "\n  The power must be disconnected before checking.\n"
    body = {"answer": ANSWER, "quotes": [SOURCE]}
    if not separated:
        body["question"] = QUESTION
    workflow = DirectedWorkflow(Response(body, {"reasoning_content": native, "api_format": "chat"}),
                                legacy_review=legacy_review, separated=separated)
    row, = workflow.directed_sft(unit())
    assert row["status"] == "eligible"
    assert workflow.calls[0][1]["capture_response_details"] is True
    assert row["messages"][-1] == {"role": "assistant", "content": ANSWER, "reasoning_content": native}
    assert row["reasoning_origin"] == "provider_reasoning" and row["reasoning_api_format"] == "chat"
    assert sft_training_messages(row["messages"])[-1]["reasoning_content"] == native
    assert "reasoning_content" not in sft_training_messages(row["messages"], drop=True)[-1]


@pytest.mark.parametrize("reasoning", [None, "", " \n "])
def test_directed_sft_accepts_nonreasoning_models_without_fake_explanation(reasoning):
    body = {"answer": ANSWER, "quotes": [SOURCE]}
    if reasoning is not None:
        body["reasoning"] = reasoning
    row, = DirectedWorkflow(Response(body)).directed_sft(unit())
    assert row["status"] == "eligible" and row["reasoning_origin"] == "none"
    assert row["messages"][-1] == {"role": "assistant", "content": ANSWER}


def test_directed_sft_retains_legacy_authored_explanation_as_synthetic():
    row, = DirectedWorkflow({"answer": ANSWER, "reasoning": "A synthetic explanation.",
                             "quotes": [SOURCE]}).directed_sft(unit())
    assert row["reasoning_origin"] == "synthetic_explanation"
    assert row["messages"][-1]["reasoning_content"] == "A synthetic explanation."


def test_directed_sft_keeps_summary_out_of_training_messages():
    row, = DirectedWorkflow(Response({"answer": ANSWER, "quotes": [SOURCE]}, {
        "reasoning_summary": "The provider summarized its reasoning.", "api_format": "responses"})).directed_sft(unit())
    assert row["status"] == "eligible" and row["reasoning_origin"] == "none"
    assert row["reasoning_summary"] == "The provider summarized its reasoning."
    assert "reasoning_content" not in row["messages"][-1]


@pytest.mark.parametrize("body", [
    {"answer": "", "quotes": [SOURCE]},
    {"answer": ANSWER, "quotes": ["An invented quote."]},
    {"answer": ANSWER, "quotes": []},
    {"answer": ANSWER, "quotes": [SOURCE], "question": "An extra question."},
    {"answer": ANSWER, "quotes": [SOURCE], "response_details": {"reasoning_content": "Spoofed native text."}},
])
def test_native_reasoning_does_not_waive_answer_quote_or_schema_checks(body):
    row, = DirectedWorkflow(Response(body, {"reasoning_content": "Native reasoning."})).directed_sft(unit())
    assert row["status"] == "quarantined"


def test_directed_native_capture_does_not_use_malformed_unused_authored_field():
    row, = DirectedWorkflow(Response({"answer": ANSWER, "quotes": [SOURCE], "reasoning": {}}, {
        "reasoning_content": "The native rationale."})).directed_sft(unit())
    assert row["status"] == "eligible" and row["reasoning_origin"] == "provider_reasoning"
    assert row["messages"][-1]["reasoning_content"] == "The native rationale."


def test_styled_directed_sft_uses_authored_explanation_and_does_not_capture_native():
    workflow = DirectedWorkflow(Response({"answer": ANSWER, "reasoning": "The selected authored style.",
        "quotes": [SOURCE]}, {"reasoning_content": "Native text.", "reasoning_summary": "Summary."}),
        style={"instruction": "Use the selected style."})
    row, = workflow.directed_sft(unit())
    assert row["reasoning_origin"] == "prompt_styled_generation"
    assert row["messages"][-1]["reasoning_content"] == "The selected authored style."
    assert "capture_response_details" not in workflow.calls[0][1]
    assert "reasoning_summary" not in row


def test_styled_directed_sft_still_requires_authored_reasoning():
    row, = DirectedWorkflow(Response({"answer": ANSWER, "quotes": [SOURCE]}, {
        "reasoning_content": "Native text."}), style={"instruction": "Use the selected style."}).directed_sft(unit())
    assert row["status"] == "quarantined"


def test_historical_directed_sft_keeps_authored_contract_on_resume():
    workflow = DirectedWorkflow(Response({"answer": ANSWER, "reasoning": "Historical explanation.",
        "quotes": [SOURCE]}, {"reasoning_content": "Native text."}), historical=True)
    row, = workflow.directed_sft(unit())
    assert row["reasoning_origin"] == "synthetic_explanation"
    assert row["messages"][-1]["reasoning_content"] == "Historical explanation."
    assert "capture_response_details" not in workflow.calls[0][1]


def test_historical_directed_sft_still_requires_json_reasoning():
    workflow = DirectedWorkflow(Response({"answer": ANSWER, "quotes": [SOURCE]}, {
        "reasoning_content": "Native text."}), historical=True)
    row, = workflow.directed_sft(unit())
    assert row["status"] == "quarantined"
    assert "capture_response_details" not in workflow.calls[0][1]
