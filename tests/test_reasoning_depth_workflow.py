"""Offline depth controls reach authored calls, review, and trainer inputs."""
from copy import deepcopy
import json

import pytest

from lib.domain.reasoning_depth import (
    apply_reasoning_depth_control, is_reasoning_depth_control_message,
    reasoning_depth_control_message, reasoning_depth_instruction, resolve_reasoning_depth,
)
from lib.domain.learner_context import learner_messages
from lib.exporters import to_chat_sample, to_sharegpt_sample
from lib.infrastructure import training_workflow as engine
from tests.test_workflow_styled_generation import (
    ANSWER, SOURCE_REASONING, TEXT, Generator, Reviewer,
    model_task_data, run, score,
)


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """All responses are injected; do not start the shared HTTP fake server."""
    yield


def _unit(identifier="depth-sample"):
    return {"id": identifier, "source_id": "depth-source", "kind": "document",
            "text": TEXT, "status": "ready", "source_name": "maintenance.txt"}


def _controls(messages):
    return [message for message in messages if is_reasoning_depth_control_message(message)]


def _workflow(tmp_path, *, targets=("sft",), node_generation=None, writer=None, reviewer=None, **options):
    output, run_id, path = run(tmp_path, targets=targets,
                               node_generation=node_generation or {
                                   "sft": {"enabled": True, "style": "default", "reasoning_depth": "deep"}},
                               **options)
    writer, reviewer = writer or Generator(), reviewer or Reviewer()
    workflow = engine.Workflow(output, run_id, tmp_path, generator=writer, jev=reviewer)
    workflow.stage = targets[0]
    return workflow, writer, reviewer, path


def test_sft_authored_depth_is_the_same_public_control_before_review_and_in_exports(tmp_path):
    workflow, writer, reviewer, path = _workflow(tmp_path)
    assert workflow.recipe["reasoning_depth_contract_version"] == 1
    row, = workflow.sft(_unit())
    assert row["status"] == "eligible", row
    control = reasoning_depth_control_message("deep")
    assert _controls(row["messages"]) == [control]
    assert reasoning_depth_instruction("deep") in writer.calls[0][0]["content"]
    # The source is teacher-only data; the public control is already in the
    # exact candidate given to the semantic reviewer, not added by an exporter.
    quality_request = model_task_data(reviewer.calls[0])
    assert quality_request["answer"] == row["messages"]
    assert quality_request["context"]["rendered_prompt"] == row["messages"][:-1]
    reviewed_payload = workflow._review_payload("sft", row)
    assert reviewed_payload["messages"] == row["messages"]
    assert learner_messages("sft", reviewed_payload) == row["messages"]
    assert engine.preferred_training_record("sft", row) == reviewed_payload
    assert to_chat_sample(reviewed_payload) == reviewed_payload
    assert to_sharegpt_sample(reviewed_payload)["system"] == control["content"]
    (path / "artifacts").mkdir(exist_ok=True)
    engine.write_trainer_export(path / "artifacts", "sft", [row])
    trainer = json.loads((path / "artifacts" / "trl_sft.jsonl").read_text("utf-8"))
    assert trainer["messages"][:-1] == row["messages"][:-1]
    assert trainer["messages"][-1]["content"] == row["messages"][-1]["content"]
    assert trainer["messages"][-1]["thinking"] == row["messages"][-1]["reasoning_content"]


class DirectedWriter(Generator):
    def __init__(self, *, separated):
        super().__init__()
        self.separated = separated

    def chat(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        self.kwargs_calls.append(dict(kwargs))
        self.usage["calls"] += 1
        candidate = {"answer": ANSWER, "reasoning": "断电是检查的必要前提，先断电再检查，最后记录检查结果。",
                     "quotes": [TEXT]}
        if not self.separated:
            candidate["question"] = "设备应如何维护？"
        return json.dumps(candidate, ensure_ascii=False)


class ContractReviewer(Reviewer):
    def chat(self, messages, **kwargs):
        if "adherence" in messages[0]["content"]:
            self.calls.append(deepcopy(messages))
            self.kwargs_calls.append(dict(kwargs))
            self.usage["calls"] += 1
            return json.dumps({"keep": True, "adherence": 5, "reason": "真实问题和回答契约一致。"})
        return super().chat(messages, **kwargs)


@pytest.mark.parametrize("separated", [False, True])
def test_directed_sft_keeps_question_literal_and_depth_in_teacher_and_student_contract(tmp_path, separated):
    writer = DirectedWriter(separated=separated)
    workflow, _, reviewer, _ = _workflow(tmp_path, writer=writer, reviewer=ContractReviewer(),
        qa_director={"enabled": True, "question_separation": separated})
    contract = {"qa_type": "closed_book", "question": "设备应如何维护？", "visible_context": "",
                "answer_policy": "answer", "guidance": "说明维护顺序。", "evidence_quotes": [TEXT],
                "learning_goal": "knowledge_application", "user_language": "zh"}
    unit = {**_unit("directed-depth"), "family_id": "depth-family", "qa_contract": contract}
    before = deepcopy(unit)
    row, = workflow.directed_sft(unit)
    assert row["status"] == "eligible", row
    assert unit == before
    control = reasoning_depth_control_message("deep")
    assert _controls(row["messages"]) == [control]
    assert [message["content"] for message in row["messages"] if message["role"] == "user"] == [contract["question"]]
    assert reasoning_depth_instruction("deep") in writer.calls[0][0]["content"]
    assert SOURCE_REASONING not in json.dumps(writer.calls, ensure_ascii=False)
    quality_request = model_task_data(reviewer.calls[0])
    assert quality_request["answer"] == row["messages"]
    assert quality_request["context"]["rendered_prompt"] == row["messages"][:-1]
    payload = engine.preferred_training_record("sft", row)
    assert payload["messages"] == row["messages"]
    assert TEXT not in json.dumps(payload, ensure_ascii=False)


def test_cot_replaces_only_upstream_depth_preserving_original_system_user_and_native_source(tmp_path):
    workflow, writer, reviewer, _ = _workflow(tmp_path, targets=("cot",),
        node_generation={"cot": {"enabled": True, "style": "default", "reasoning_depth": "deep"}})
    prompt = apply_reasoning_depth_control([
        {"role": "system", "content": "Original public instruction; preserve it exactly."},
        {"role": "user", "content": "设备应如何维护？\n保留用户原始换行。"},
    ], "quick")
    sample = {"id": "cot-depth", "source_id": "depth-source", "status": "eligible",
              "evidence_level": "source_and_model_assessed",
              "source_context": {"kind": "document", "text": TEXT}, "quotes": [TEXT],
              "judge": score(), "reasoning_origin": "provider_reasoning",
              "messages": [*prompt, {"role": "assistant", "content": ANSWER,
                                      "reasoning_content": SOURCE_REASONING}]}
    before = deepcopy(sample)
    row, = workflow.styled_cot(sample, workflow.generation_style("cot", sample["id"]))
    assert row["status"] == "eligible", row
    assert sample == before
    assert _controls(row["question"]) == [reasoning_depth_control_message("deep")]
    assert row["question"][0] == prompt[0]
    assert row["question"][-1] == prompt[-1]
    assert row["source_context"] == sample["source_context"]
    assert SOURCE_REASONING not in json.dumps(writer.calls, ensure_ascii=False)
    assert reasoning_depth_instruction("deep") in writer.calls[0][0]["content"]
    payload = workflow._review_payload("cot", row)
    assert payload["question"] == row["question"]
    visible = learner_messages("cot", payload)
    assert _controls(visible) == [reasoning_depth_control_message("deep")]
    assert visible[0] == prompt[0] and visible[-2] == prompt[-1]
    assert visible[-1]["reasoning_content"] == "\n".join(row["reasoning"])
    # Both system instructions must survive ShareGPT conversion.
    sharegpt = to_sharegpt_sample({"messages": visible})
    assert sharegpt["system"] == prompt[0]["content"] + "\n\n" + reasoning_depth_control_message("deep")["content"]


def test_mixed_runtime_depth_is_not_pinned_to_snapshot_and_is_stable_on_real_retry(tmp_path):
    workflow, writer, reviewer, _ = _workflow(tmp_path,
        node_generation={"sft": {"enabled": True, "style": "default", "reasoning_depth": "mixed"}},
        reviewer=Reviewer(reject_style=1))
    samples = [f"depth-family-{index}" for index in range(90)]
    actual = {identifier: workflow.generation_style("sft", identifier)["reasoning_depth"] for identifier in samples}
    assert set(actual.values()) == {"quick", "standard", "deep"}
    assert actual == {identifier: resolve_reasoning_depth("mixed", identifier) for identifier in samples}
    snapshot = workflow.recipe["node_generation_prompts"]["sft"]["default"]
    assert all(reasoning_depth_instruction(depth) not in snapshot for depth in actual.values())
    identifier = next(key for key, value in actual.items() if value == "deep")
    row, = workflow.sft(_unit(identifier))
    assert row["status"] == "eligible" and row["repair_attempts"] == 1, row
    assert len(writer.calls) == 2
    for call in writer.calls:
        assert reasoning_depth_instruction("deep") in call[0]["content"]
        assert reasoning_depth_instruction("quick") not in call[0]["content"]
    assert _controls(row["messages"]) == [reasoning_depth_control_message("deep")]


def test_frozen_recipe_without_marker_retains_old_authored_wire_and_training_input(tmp_path):
    workflow, writer, _, _ = _workflow(tmp_path)
    workflow.recipe.pop("reasoning_depth_contract_version")
    # A depth field cannot implicitly migrate a frozen recipe.
    style = workflow.generation_style("sft", "legacy-depth")
    assert "reasoning_depth" not in style and "reasoning_depth_instruction" not in style
    row, = workflow.sft(_unit("legacy-depth"))
    assert row["status"] == "eligible", row
    assert _controls(row["messages"]) == []
    assert "<reasoning_depth_control" not in json.dumps(writer.calls, ensure_ascii=False)
    assert reasoning_depth_instruction("deep") not in writer.calls[0][0]["content"]


class DialogueWriter(Generator):
    def __init__(self):
        super().__init__()
        self.user_calls = []
        self.answer_calls = []

    def chat(self, messages, **kwargs):
        instruction = messages[0]["content"]
        if "你只设计下一条真实用户发言" in instruction:
            self.calls.append(deepcopy(messages))
            self.kwargs_calls.append(dict(kwargs))
            self.usage["calls"] += 1
            self.user_calls.append(deepcopy(messages))
            text = "设备维护前我应该先做什么？" if len(self.user_calls) == 1 else "检查结束了，我还要记什么？"
            return json.dumps({"message": text}, ensure_ascii=False)
        if "只回答最新用户消息" in instruction:
            self.calls.append(deepcopy(messages))
            self.kwargs_calls.append(dict(kwargs))
            self.usage["calls"] += 1
            self.answer_calls.append(deepcopy(messages))
            value = ({"answer": "维护前先切断电源，然后检查线路。",
                      "reasoning": "维护前断电是检查线路的前置条件，需满足此条件再开始检查。"}
                     if len(self.answer_calls) == 1 else
                     {"answer": "记录检查结果。", "reasoning": "检查已结束，现在应记录此次检查的结果。"})
            return json.dumps({**value, "quotes": [TEXT]}, ensure_ascii=False)
        return super().chat(messages, **kwargs)


@pytest.mark.parametrize("separated", [False, True])
def test_multiturn_depth_preserves_real_turn_indices_and_every_reviewed_prefix(tmp_path, separated):
    writer = DialogueWriter()
    workflow, _, reviewer, _ = _workflow(tmp_path, targets=("multiturn",), writer=writer,
        node_generation={"multiturn": {"enabled": True, "style": "default", "reasoning_depth": "deep"}},
        conversation_turns=2, qa_director={"enabled": separated, "question_separation": separated})
    row, = workflow.multiturn(_unit("dialogue-depth"))
    assert row["status"] == "eligible", row
    assert row["turn_count"] == 2
    assert [turn["end_message_index"] for turn in row["turn_reviews"]] == [2, 4]
    assert [message["role"] for message in row["messages"]] == ["system", "user", "assistant", "user", "assistant"]
    assert _controls(row["messages"]) == [reasoning_depth_control_message("deep")]
    assert len(writer.answer_calls) == 2
    for call in writer.answer_calls:
        assert reasoning_depth_instruction("deep") in call[0]["content"]
        if separated:
            assert reasoning_depth_control_message("deep")["content"] in call[0]["content"]
        else:
            assert _controls(model_task_data(call)["messages"]) == [reasoning_depth_control_message("deep")]
        assert call[-1]["role"] == "user"
    turn_checks = [model_task_data(call) for call in reviewer.calls
                   if "dialogue_before_answer" in model_task_data(call).get("context", {})]
    assert len(turn_checks) == 2
    for index, request in enumerate(turn_checks):
        end = row["turn_reviews"][index]["end_message_index"]
        assert request["context"]["dialogue_before_answer"] == row["messages"][:end]
        assert request["answer"] == row["messages"][end]
    assert workflow._review_payload("multiturn", row)["messages"] == row["messages"]
    assert to_chat_sample(engine.preferred_training_record("multiturn", row))["messages"] == row["messages"]


@pytest.mark.parametrize("upstream_depth", [None, "quick", "deep"])
def test_actual_cot_route_never_globally_relabels_previous_assistant_explanations(tmp_path, upstream_depth):
    from lib.domain.workflow_reasoning_route import cot_with_sft_context, finalized_sft_rows

    workflow, writer, reviewer, _ = _workflow(tmp_path, targets=("sft", "cot"), node_generation={
        "sft": {"enabled": True, "style": "default", "reasoning_depth": "quick"},
        "cot": {"enabled": True, "style": "default", "reasoning_depth": "deep"}})
    workflow.stage = "cot"
    earlier_native = "EARLIER_NATIVE_TRACE: the precondition is retained exactly."
    original_messages = [
        {"role": "user", "content": "先告诉我检查前需要做什么。"},
        {"role": "assistant", "content": "检查前先断电。", "reasoning_content": earlier_native},
        {"role": "user", "content": "接着说明设备维护的完整顺序。\n保留这条消息。"},
        {"role": "assistant", "content": ANSWER, "reasoning_content": SOURCE_REASONING},
    ]
    if upstream_depth:
        original_messages = apply_reasoning_depth_control(original_messages, upstream_depth)
    original_ends = [index for index, message in enumerate(original_messages) if message["role"] == "assistant"]
    sample = {"id": "linked-cot-depth", "source_id": "depth-source", "status": "eligible",
              "source_context": {"kind": "document", "text": TEXT}, "quotes": [TEXT],
              "judge": score(), "evidence_level": "source_and_model_assessed",
              "reasoning_origin": "provider_reasoning", "messages": original_messages,
              "turn_reviews": [{"turn": number, "end_message_index": end,
                                "reasoning_origin": "provider_reasoning", "judge": score()}
                               for number, end in enumerate(original_ends, 1)]}
    before = deepcopy(sample)
    if upstream_depth != "deep":
        with pytest.raises(ValueError, match="^reasoning_depth_history_control_mismatch$"):
            workflow.cot(sample)
        assert writer.calls == reviewer.calls == []
        assert sample == before
        # The pure route must reject an untrusted result trying to bypass the
        # pre-call guard with a newly labelled prompt and only a new last turn.
        forged_question = apply_reasoning_depth_control(
            workflow.without_source_reasoning(sample["messages"][:-1]), "deep")
        with pytest.raises(ValueError, match="^reasoning_depth_history_control_mismatch$"):
            cot_with_sft_context(sample, {"status": "eligible", "question": forged_question,
                "generation_style": {"reasoning_depth": "deep"},
                "reasoning": ["New last-turn explanation."], "answer": ANSWER})
        return
    row, = workflow.cot(sample)
    assert row["status"] == "eligible", row
    assert sample == before
    assert row["reasoning_route"] == "sft_via_cot"
    assert _controls(row["messages"]) == _controls(row["question"]) == [reasoning_depth_control_message("deep")]
    assert [review["end_message_index"] for review in row["turn_reviews"]] == [2, 4]
    assert row["messages"][2]["reasoning_content"] == earlier_native
    assert earlier_native not in json.dumps(writer.calls, ensure_ascii=False)
    assert row["reasoning_depth_route_audit"]["source_turn_reviews"] == sample["turn_reviews"]
    assert row["reasoning_depth_route_audit"]["scope"] == "upstream_message_index_projection_not_new_review"
    assert [message for message in row["messages"] if message["role"] == "user"] == [
        message for message in sample["messages"] if message["role"] == "user"]
    final_sft, = finalized_sft_rows([sample], [row])
    assert final_sft["messages"] == row["messages"]
    assert final_sft["turn_reviews"] == row["turn_reviews"]
    assert workflow._review_payload("sft", final_sft)["messages"] == row["messages"]
    assert learner_messages("cot", workflow._review_payload("cot", row)) == row["messages"]


@pytest.mark.parametrize("upstream_depth", [None, "quick"])
def test_single_turn_cot_can_select_new_depth_and_rebind_its_current_target_index(tmp_path, upstream_depth):
    from lib.domain.workflow_reasoning_route import finalized_sft_rows

    workflow, _, _, _ = _workflow(tmp_path, targets=("sft", "cot"), node_generation={
        "cot": {"enabled": True, "style": "default", "reasoning_depth": "deep"}})
    workflow.stage = "cot"
    original = [{"role": "user", "content": "设备维护应如何安排？"},
                {"role": "assistant", "content": ANSWER, "reasoning_content": SOURCE_REASONING}]
    if upstream_depth:
        original = apply_reasoning_depth_control(original, upstream_depth)
    old_end = len(original) - 1
    sample = {"id": "single-cot-depth", "source_id": "depth-source", "status": "eligible",
              "source_context": {"kind": "document", "text": TEXT}, "quotes": [TEXT],
              "judge": score(), "evidence_level": "source_and_model_assessed",
              "reasoning_origin": "provider_reasoning", "messages": original,
              "turn_reviews": [{"turn": 1, "end_message_index": old_end, "reasoning_origin": "provider_reasoning"}]}
    before = deepcopy(sample)
    row, = workflow.cot(sample)
    assert row["status"] == "eligible", row
    assert sample == before
    assert _controls(row["messages"]) == _controls(row["question"]) == [reasoning_depth_control_message("deep")]
    assert row["turn_reviews"][0]["end_message_index"] == 2
    assert row["reasoning_depth_route_audit"]["source_turn_reviews"][0]["end_message_index"] == old_end
    final, = finalized_sft_rows([sample], [row])
    assert final["messages"] == row["messages"]
    assert final["turn_reviews"][0]["end_message_index"] == 2
    assert learner_messages("cot", workflow._review_payload("cot", row)) == final["messages"]


@pytest.mark.parametrize("changed_field", ["user", "system"])
def test_cot_depth_projection_cannot_change_any_other_learner_input(changed_field):
    from lib.domain.workflow_reasoning_route import cot_with_sft_context

    messages = [{"role": "system", "content": "Keep the original public instruction."},
                {"role": "user", "content": "Keep the exact task."},
                {"role": "assistant", "content": "Previous answer.", "reasoning_content": "Previous native text."}]
    sample = {"id": "unchanged-task", "source_id": "source", "status": "eligible", "messages": messages}
    question = apply_reasoning_depth_control(deepcopy(messages[:-1]), "deep")
    next(message for message in question if message["role"] == changed_field)["content"] = "Changed task or instruction."
    with pytest.raises(ValueError, match="^cot_sft_lineage_mismatch$"):
        cot_with_sft_context(sample, {"status": "eligible", "question": question,
            "generation_style": {"reasoning_depth": "deep"}, "reasoning": ["New explanation."], "answer": "New answer."})
    assert messages[0]["content"] == "Keep the original public instruction."
    assert messages[1]["content"] == "Keep the exact task."
