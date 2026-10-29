"""Imported explanations remain attributable through training and report projection."""
from copy import deepcopy

import pytest

from lib.contexts.workflows.application.generate_sft import GenerateSft
from lib.contexts.workflows.application.generate_dialogue import GenerateMultiturnDialogue
from lib.contexts.workflows.application.generate_cot import GenerateCot
from lib.contexts.workflows.application.trim_reasoning import TrimReasoning
from lib.contexts.workflows.domain.dialogue import DialogueSource
from lib.contexts.workflows.domain.targets import training_record
from lib.contexts.review.domain.candidate_content import _manual_patch, preserve_provider_reasoning
from lib.contexts.review.infrastructure.automatic_runtime import WorkflowReviewRepair
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


@pytest.mark.parametrize("edit,expected_origin", [
    ("human", "human_revision"), ("auto", "prompt_revision"), ("trim", "prompt_trim"),
])
def test_shared_revision_paths_retire_import_receipt_after_body_changes(edit, expected_origin):
    original = GenerateSft(PreservingPort(), {"brief": "", "review_repair": {}}).execute(imported_unit())[0]
    snapshot = deepcopy(original)
    explanation = "After isolating the power, I can inspect the cable safely."
    messages = deepcopy(original["messages"])
    messages[-1]["reasoning_content"] = explanation
    if edit == "human":
        updated = _manual_patch("sft", original, {"messages": messages})
    elif edit == "auto":
        class RepairProbe(WorkflowReviewRepair):
            recipe = {"package_review": {}, "generation_preferences": {}}

            def __init__(self):
                self.state = {"stages": {"review": {}}}

            def save(self, **options):
                pass

            def _review_payload(self, target, row):
                return training_record(target, row)

            def ask(self, key, role, prompt, data, **options):
                assert prompt == "workflow.review_repair"
                return {"record": {"messages": deepcopy(messages)}, "quotes": [], "uncertain": False}

        updated, issue = RepairProbe()._repair_content("sft", original,
            {"source_kind": "conversation", "teacher_evidence": original["source_context"]},
            ["revision"], PreservingPort().assess(), "Clarify the explanation.")
        assert issue is None
    else:
        class TrimPort(PreservingPort):
            def request(self, *args, **options):
                return {"reasoning": explanation}

            def check_adherence(self, *args, **options):
                return {"keep": True, "adherence": 5}

        updated, = TrimReasoning(TrimPort(), {"reasoning_trim": {"template": "custom"},
            "reasoning_trim_prompt": "Keep the explanation concise."}).execute(
                {"target": "sft", "sample": original})
    assert original == snapshot
    assert updated["messages"][-1]["reasoning_content"] == explanation
    assert updated["reasoning_origin"] == expected_origin
    assert updated["conversation_reasoning_scope"] == "input_only"
    assert updated["conversation_reasoning"] == snapshot["conversation_reasoning"]
    projected = report_sample("sft", updated, training_record("sft", updated), released=False, position=0)
    document = render_report_html({"run_id": "run-1", "name": "Import", "status": "completed"},
        {"target": "sft", "rows": [projected], "total": 1, "offset": 0, "limit": 6}, language="zh")
    assert "此收据描述导入阶段；后续节点可能已改写输出。" in document
    assert "导入消息已保留；该记录不代表质量审核通过。" not in document
    updated["conversation_reasoning"]["turns"].clear()
    assert original == snapshot


@pytest.mark.parametrize("original_origin", ["source", "synthetic_explanation", "none"])
def test_cot_revision_replaces_only_current_import_origin(original_origin):
    sft = GenerateSft(PreservingPort(), {"brief": "", "review_repair": {}}).execute(imported_unit())[0]
    original = GenerateCot(PreservingPort(), {"review_repair": {}}).extract(sft)[0]
    original["reasoning_origin"] = original_origin
    updated = _manual_patch("cot", original, {"answer": "Inspect the cable after disconnecting the power."})
    assert updated["conversation_reasoning_scope"] == "input_only"
    assert updated["conversation_reasoning"] == original["conversation_reasoning"]
    assert updated["reasoning_origin"] == "human_revision"


@pytest.mark.parametrize("scope", ["input_messages_preserved", "input_only"])
def test_scoring_or_unchanged_body_does_not_retire_import_receipt(scope):
    original = GenerateSft(PreservingPort(), {"brief": "", "review_repair": {}}).execute(imported_unit())[0]
    original["conversation_reasoning_scope"] = scope
    assert _manual_patch("sft", original, {"messages": original["messages"]}) == original
    updated = deepcopy(original)
    updated["judge"] = PreservingPort().assess()
    expected = deepcopy(updated)
    preserve_provider_reasoning("sft", original, updated, origin="prompt_revision")
    assert updated == expected


@pytest.mark.parametrize("scope,current_origin", [
    ("input_messages_preserved", "provider_reasoning"),
    ("input_only", "prompt_styled_generation"),
    ("input_only", "synthetic_explanation"),
])
def test_historical_import_receipt_cannot_override_current_output_origin(scope, current_origin):
    original = GenerateSft(PreservingPort(), {"brief": "", "review_repair": {}}).execute(imported_unit())[0]
    original["conversation_reasoning_scope"] = scope
    updated = deepcopy(original)
    updated["messages"][-1]["reasoning_content"] = "A separately generated explanation."
    updated["reasoning_origin"] = current_origin
    preserve_provider_reasoning("sft", original, updated, origin="prompt_revision")
    assert updated["conversation_reasoning_scope"] == "input_only"
    assert updated["conversation_reasoning"] == original["conversation_reasoning"]
    assert updated["reasoning_origin"] == current_origin


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
