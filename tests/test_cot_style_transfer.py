"""Upstream result import and bounded, answer-preserving style transfer."""
from copy import deepcopy
import json

import pytest

from lib.contexts.datasets.contracts import adapt_reasoning_record
from lib.contexts.workflows.application.generate_cot import GenerateCot
from lib.contexts.workflows.contracts import training_record
from lib.domain.workflow_node_prompts import active_node_prompt_ids
from test_generation_node_use_cases import MemoryGenerationPort, conversation, ANSWER, REASONING, score
from test_integrated_reflection import body, recipe


def replacement():
    value = body()
    value.pop("answer")
    return value


def test_restylers_receive_original_but_never_regenerate_answer_or_certify_by_matching_it():
    original = conversation()
    before = deepcopy(original)
    port = MemoryGenerationPort([replacement()])
    row, = GenerateCot(port, recipe("faithful_restyle")).generate_styled(original, {"instruction": "保留条件。"})
    assert row["status"] == "eligible"
    assert len(port.calls) == 2
    assert port.calls[0][2] == "workflow.cot_rationale"
    task = port.calls[0][3]
    assert task["style_transfer"]["original"] == {"reasoning": REASONING, "answer": ANSWER}
    assert "reference_answer_status" not in task and "reference_answer" not in task
    assert port.calls[0][4]["structured_response_attempts"] == 1
    assert port.calls[1][1] == "assessment"
    assert row["style_transfer"]["answer_preserved"] is True
    assert row["style_transfer"]["semantic_status"] == "model_assessed"
    assert row["reasoning"] == [REASONING] and row["answer"] == ANSWER
    assert training_record("cot", row) == {"question": original["messages"][:-1], "reasoning": [REASONING], "answer": ANSWER}
    assert original == before
    assert active_node_prompt_ids("cot", "Agent 上下文", node_generation=recipe("faithful_restyle")["node_generation"]) == (
        "workflow.cot_rationale", "workflow.rationale_check")


def test_existing_answer_is_not_truth_and_failed_semantics_retains_both_versions():
    class RejectPort(MemoryGenerationPort):
        def assess(self, key, context, candidate, **options):
            assert context["style_transfer"]["original"]["answer"] == ANSWER
            assert "原答案不一定正确" in options["instruction"]
            self.calls.append((key, "assessment", candidate))
            return {**score(), "keep": False, "reasoning_valid": False, "reason": "原答案缺少必要条件。"}
    port = RejectPort([replacement()])
    row, = GenerateCot(port, recipe("faithful_restyle")).generate_styled(conversation(), {"instruction": "自然表达。"})
    assert row["status"] == "quarantined" and len(port.calls) == 2
    assert row["reasoning"] == [REASONING] and row["answer"] == ANSWER
    assert row["style_transfer"]["original"]["reasoning"] == REASONING
    assert row["style_transfer"]["semantic_status"] == "not_accepted"


def test_missing_trace_spends_no_calls_and_illegal_answer_is_not_silently_dropped():
    sample = conversation()
    sample["messages"][-1].pop("reasoning_content")
    port = MemoryGenerationPort()
    row, = GenerateCot(port, recipe("faithful_restyle")).generate_styled(sample, {"instruction": "保留条件。"})
    assert row["reason"] == "cot_style_transfer_missing_reasoning" and port.calls == []
    assert row["source_context"] == sample
    illegal = {**replacement(), "answer": "改掉的答案"}
    port = MemoryGenerationPort([illegal])
    row, = GenerateCot(port, recipe("faithful_restyle")).generate_styled(conversation(), {"instruction": "保留条件。"})
    assert row["status"] == "quarantined" and len(port.calls) == 1
    assert row["feedback"]["raw_response"] == illegal
    assert row["reasoning"] == [REASONING] and row["answer"] == ANSWER


@pytest.mark.parametrize("format_name", ["nemo", "openthoughts"])
def test_actual_upstream_envelopes_preserve_history_trace_and_untrusted_scores(format_name):
    if format_name == "nemo":
        source = {"problem": "维护时先做什么？", "generation": f"<think>{REASONING}</think>{ANSWER}", "is_correct": True}
    else:
        source = {"system": "保留必要条件。", "conversations": [
            {"from": "user", "value": "先记结果？"}, {"from": "assistant", "value": "先确认操作条件。"},
            {"from": "user", "value": "维护时先做什么？"},
            {"from": "assistant", "value": f"<|begin_of_thought|>\n{REASONING}\n<|end_of_thought|>\n<|begin_of_solution|>\n{ANSWER}\n<|end_of_solution|>"}]}
    original = deepcopy(source)
    adapted = adapt_reasoning_record(source)
    from lib.review_editor import normalize_sample
    sample = normalize_sample(adapted)
    assert sample["messages"][-1] == {**({"from": "assistant", "value": ANSWER} if format_name != "nemo" else {}),
        "role": "assistant", "content": ANSWER, "reasoning_content": REASONING}
    assert sample["reasoning_import"]["quality"] == "unassessed"
    assert len(sample["messages"]) == (2 if format_name == "nemo" else 5)
    assert source == original


@pytest.mark.parametrize("text", ["<think>unfinished", "<think></think>answer",
    "<think>a</think><think>b</think>answer",
    "<|begin_of_thought|>a<|begin_of_solution|>b<|end_of_solution|>"])
def test_damaged_upstream_delimiters_are_never_guessed(text):
    with pytest.raises(ValueError, match="reasoning_import_"):
        adapt_reasoning_record({"problem": "Question", "generation": text})


def test_plain_answers_and_other_conversations_are_not_invented_reasoning():
    assert adapt_reasoning_record({"messages": [{"role": "assistant", "content": "An ordinary answer."}]}) is None
    value = adapt_reasoning_record({"problem": "Q", "generation": "A"})
    assert "reasoning_content" not in value["messages"][-1]
    with pytest.raises(ValueError, match="conflicting_reasoning"):
        adapt_reasoning_record({"messages": [{"role": "assistant", "content": "<think>a</think>b", "reasoning_content": "c"}]})


def test_disk_import_edit_export_and_restart_preserve_original_and_legacy_import(tmp_path):
    from lib.infrastructure import training_workflow as engine
    from test_workflow_styled_generation import model_task_data, Reviewer
    source = tmp_path / "nemo.jsonl"
    raw = {"problem": "维护时先做什么？", "generation": f"<think>{REASONING}</think>{ANSWER}", "is_correct": True}
    source.write_text(json.dumps(raw, ensure_ascii=False) + "\n", encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["cot"], node_generation=recipe("faithful_restyle")["node_generation"])
    path = engine.run_path(output, run_id)

    class Author:
        calls = 0

        def chat(self, messages, **kwargs):
            self.calls += 1
            task = model_task_data(messages)
            assert task["style_transfer"]["original"] == {"reasoning": REASONING, "answer": ANSWER}
            return json.dumps(replacement(), ensure_ascii=False)

    author, reviewer = Author(), Reviewer()
    workflow = engine.Workflow(output, run_id, tmp_path, generator=author, judge=reviewer)
    state = workflow.execute()
    assert state["status"] == "completed", json.dumps(state, ensure_ascii=False)
    row, = engine.read_json(path / "artifacts/cot.records.json")
    assert row["status"] == "eligible" and row["style_transfer"]["original"]["reasoning"] == REASONING
    assert author.calls == 1 and len(reviewer.calls) == 1
    exported = (path / "artifacts/cot.jsonl").read_bytes()
    assert json.loads(exported)["answer"] == ANSWER
    assert "style_transfer" not in json.loads(exported)
    engine.Workflow(output, run_id, tmp_path, generator=author, judge=reviewer).execute()
    assert author.calls == 1 and len(reviewer.calls) == 1
    assert (path / "artifacts/cot.jsonl").read_bytes() == exported
    assert json.loads(source.read_text(encoding="utf-8")) == raw

    # Old recipes do not reinterpret previously opaque generation fields.
    legacy_id = engine.create_run(output, sources=[source], targets=["cot"])
    legacy = engine.Workflow(output, legacy_id, tmp_path, generator=author, judge=reviewer)
    legacy.recipe.pop("external_reasoning_import_version")
    units = list(legacy.iter_source_units(legacy.recipe["sources"][0]))
    assert all(unit["status"] == "quarantined" for unit in units)
