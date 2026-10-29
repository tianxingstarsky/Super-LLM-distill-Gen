"""Imported explanations remain attributable through training and report projection."""
from copy import deepcopy

import pytest

from lib.contexts.workflows.application.generate_sft import GenerateSft
from lib.contexts.workflows.application.generate_dialogue import GenerateMultiturnDialogue
from lib.contexts.workflows.application.generate_cot import GenerateCot
from lib.contexts.workflows.domain.dialogue import DialogueSource
from lib.contexts.workflows.domain.targets import training_record
from lib.domain.workflow_report import report_sample
from lib.infrastructure.workflow_report import render_report_html


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield  # These use cases have in-memory ports; no service is contacted.


def imported_unit(*, final_synthetic=True):
    messages = [
        {"role": "user", "content": "What should I do first?"},
        {"role": "assistant", "content": "Disconnect the power.", "reasoning_content": "Power isolation prevents an accidental start."},
        {"role": "user", "content": "And then?"},
        {"role": "assistant", "content": "Inspect the cable.", "reasoning_content": "The equipment is isolated, so I can inspect the visible cable safely."},
    ]
    return {"id": "conversation-1", "source_id": "source-1", "kind": "conversation",
        "status": "ready", "messages": messages, "tools": [],
        "conversation_reasoning": {"version": 1, "mode": "fill_missing", "assistant_turns": 2,
            "existing_turns": 1, "missing_turns": 1, "synthesized_turns": 1,
            "remaining_missing_turns": 0, "semantic_status": "not_assessed", "turns": [
                {"message_index": 1, "origin": "source" if final_synthetic else "synthetic_explanation"},
                {"message_index": 3, "origin": "synthetic_explanation" if final_synthetic else "source"}]}}


class PreservingPort:
    def generation_style(self, *args):
        return None

    def prepare_source(self, unit, style):
        return DialogueSource(deepcopy(unit))

    def assess(self, *args, **kwargs):
        return {"keep": True, "grounded": True, "reasoning_valid": True,
                "correctness": 4, "scores": {"reasoning": 4}}

    def reject(self, unit, reason):
        return {"id": unit["id"], "source_id": unit["source_id"], "status": "quarantined", "reason": reason}

    def request(self, *args, **kwargs):
        raise AssertionError("Copying imported messages must not request generation")


@pytest.mark.parametrize("node,target", [(GenerateSft, "sft"), (GenerateMultiturnDialogue, "multiturn")])
def test_preserved_output_keeps_per_turn_provenance_outside_training_fields(node, target):
    unit = imported_unit()
    original = deepcopy(unit)
    row = node(PreservingPort(), {"brief": "", "review_repair": {}}).execute(unit)[0]
    assert row["reasoning_origin"] == "synthetic_explanation"
    assert row["conversation_reasoning_scope"] == "input_messages_preserved"
    assert row["conversation_reasoning"]["semantic_status"] == "not_assessed"
    assert training_record(target, row) == {"messages": original["messages"]}
    assert unit == original
    row["conversation_reasoning"]["turns"].clear()
    assert unit == original


@pytest.mark.parametrize("final_synthetic", [False, True])
def test_cot_origin_comes_from_extracted_turn_not_other_assistants(final_synthetic):
    unit = imported_unit(final_synthetic=final_synthetic)
    sft = GenerateSft(PreservingPort(), {"brief": "", "review_repair": {}}).execute(unit)[0]
    cot = GenerateCot(PreservingPort(), {"review_repair": {}}).extract(sft)[0]
    assert cot["reasoning_origin"] == ("synthetic_explanation" if final_synthetic else "source")
    assert cot["reasoning"] == [unit["messages"][-1]["reasoning_content"]]
    assert cot["answer"] == unit["messages"][-1]["content"]
    assert "conversation_reasoning" not in training_record("cot", cot)


def test_rewritten_cot_receipt_cannot_claim_new_reasoning_was_imported():
    class StyledPort(PreservingPort):
        def generation_style(self, *args):
            return {"instruction": "Explain concisely"}

        def generate_styled_cot(self, sample, style):
            return [{"id": sample["id"], "source_id": sample["source_id"], "status": "eligible",
                     "question": sample["messages"][:-1], "answer": "Rewritten answer",
                     "reasoning": ["New explanation"], "reasoning_origin": "prompt_styled_generation"}]
    unit = imported_unit()
    row = GenerateCot(StyledPort(), {}).extract(unit)[0]
    assert row["reasoning_origin"] == "prompt_styled_generation"
    assert row["conversation_reasoning_scope"] == "input_only"


def test_extracting_regenerated_sft_keeps_its_input_receipt_separate():
    unit = imported_unit()
    sft = GenerateSft(PreservingPort(), {"brief": "", "review_repair": {}}).execute(unit)[0]
    sft.update(conversation_reasoning_scope="input_only", reasoning_origin="prompt_styled_generation")
    cot = GenerateCot(PreservingPort(), {"review_repair": {}}).extract(sft)[0]
    assert cot["conversation_reasoning_scope"] == "input_only"
    assert cot["reasoning_origin"] == "prompt_styled_generation"


@pytest.mark.parametrize("language,expected", [
    ("zh", ("合成补全 1 轮", "消息 4 的助手发言", "并非恢复原模型的真实思考")),
    ("en", ("1 synthesized", "Assistant at message 4", "not recovered original thoughts")),
])
def test_report_exposes_original_and_synthetic_turns_without_claiming_review(language, expected):
    unit = imported_unit()
    row = GenerateSft(PreservingPort(), {"brief": "", "review_repair": {}}).execute(unit)[0]
    body = training_record("sft", row)
    projected = report_sample("sft", row, body, released=False, position=0)
    assert projected["provenance"]["conversation_reasoning"] == unit["conversation_reasoning"]
    assert not any(projected["reviews"].values())
    document = render_report_html({"run_id": "run-1", "name": "Import", "status": "completed"},
        {"target": "sft", "rows": [projected], "total": 1, "offset": 0, "limit": 6}, language=language)
    for text in expected:
        assert text in document
    assert projected["training_body"] == {"messages": unit["messages"]}
