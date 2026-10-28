"""Real workflow boundary: native channels, source scope, restart and coverage."""
from copy import deepcopy
import json
import pytest

from lib.infrastructure import training_workflow as engine
from lib.domain.workflow_node_prompts import active_node_prompt_ids
from lib.domain.workflow_generation import validate_node_generation
from lib.contexts.workflows.domain.integrated_reflection import default_integrated_reflection
from lib.contexts.workflows.domain.authored_task_contract import compile_task_contract
from lib.contexts.workflows.domain.native_cot import NATIVE_EXECUTION_STATE, native_execution_state
from lib.contexts.workflows.contracts import training_record
from lib.contexts.workflows.infrastructure.native_cot_messages import native_cot_messages
from lib.io_utils import atomic_json


def config():
    return {"cot": {"enabled": True, "style": "skeptical", "instruction": "",
        "answer_mode": "native_joint", "self_check": default_integrated_reflection()}}


@pytest.mark.parametrize("native", ["  用户要比较差异。我需要先确认指标和实际结果；没有测量不能认定改善。\n", ""])
@pytest.mark.parametrize("environment_version", [None, 1])
def test_one_actual_request_captures_exact_channels_and_resumes_without_json(tmp_path, native, environment_version):
    source = tmp_path / "chat.jsonl"
    original = {"messages": [{"role": "user", "content": "我做了对照，就能认定模型提升吗？"},
        {"role": "assistant", "content": "做过对照就已经证明提升。", "reasoning_content": "历史错误不能充当证据。"},
        {"role": "user", "content": "没有测量结果就能认定模型提升吗？"},
        {"role": "assistant", "content": "一定提升了。", "reasoning_content": "旧的错误推理。"}]}
    source.write_text(json.dumps(original, ensure_ascii=False), encoding="utf8")
    output = tmp_path / "output"
    rid = engine.create_run(output, sources=[source], targets=["cot"], node_generation=config(),
        agent_experience={"reasoning": {"enabled": True, "text": "PROFILE_LITERAL_{unchanged}"}})
    path = engine.run_path(output, rid)
    recipe = engine.read_json(path / "recipe.json")
    assert recipe["native_environment_version"] == 1
    if environment_version is None:
        # A queued historical recipe has no marker; never migrate it on resume.
        recipe.pop("native_environment_version")
        state = engine.read_json(path / "state.json")
        state["recipe_hash"] = engine.digest(recipe)
        atomic_json(path / "recipe.json", recipe)
        atomic_json(path / "state.json", state)
    class Author:
        calls = 0
        reasoning = {"mode": "enabled"}
        def chat(self, messages, **options):
            self.calls += 1
            self.last_messages = deepcopy(messages)
            assert messages[-1] == original["messages"][-2]
            assert {"role": "assistant", "content": original["messages"][1]["content"]} in messages
            assert "历史错误不能充当证据。" not in json.dumps(messages, ensure_ascii=False)
            assert "一定提升了。" in messages[0]["content"]
            assert "旧的错误推理。" in messages[0]["content"]
            assert not options.get("json_output") and options["retries"] == 1
            options["response_details"].update(api_format="chat", reasoning_content=native)
            return "还不能认定提升，需要实际测量结果，并说明比较条件。"
    author = Author()
    workflow = engine.Workflow(output, rid, tmp_path, generator=author)
    workflow.stage = "cot"
    sample = {**original, "id": "native", "source_id": "source", "evidence_level": "unassessed"}
    row, = workflow.cot(sample)
    assert row["reasoning"] == [native]
    assert "PROFILE_LITERAL_{unchanged}" in row["question"][0]["content"]
    assert row["question"][0] in author.last_messages
    assert row["answer"] != original["messages"][-1]["content"]
    assert row["reasoning_origin"] == "provider_reasoning"
    assert "judge" not in row and row["cot_review_policy"]["semantic_status"] == "not_assessed"
    assert row["status"] == ("eligible" if native else "quarantined")
    if environment_version == 1:
        expected = {"version": 1, "current_user_turn": 2, "source_scope": "available_context",
            "assistant_history": "unverified", "previous_candidate": "proposal", "tools": "none"}
        encoded_state = author.last_messages[0]["content"].split("<native_execution_state>\n", 1)[1].split("\n</native_execution_state>", 1)[0]
        assert json.loads(encoded_state) == expected == row["native_generation"]["execution_state"]
        assert len(encoded_state) < 256
    else:
        assert "execution_state" not in row["native_generation"]
        assert NATIVE_EXECUTION_STATE not in json.dumps(author.last_messages)
    exported = training_record("cot", row)
    assert exported == {"question": row["question"], "reasoning": [native], "answer": row["answer"]}
    assert NATIVE_EXECUTION_STATE not in json.dumps(exported)
    resumed = engine.Workflow(output, rid, tmp_path, generator=author)
    resumed.stage = "cot"
    assert resumed.cot(sample) == [row]
    assert author.calls == 1 and json.loads(source.read_text(encoding="utf8")) == original
    assert engine.read_json(path / "recipe.json") == recipe
    assert active_node_prompt_ids("cot", "Agent 上下文", node_generation=config()) == ("workflow.cot_native_joint",)


def test_excerpt_and_roles_not_replaced_by_editorial_draft():
    task = {"prompt": [{"role": "user", "content": "Source material:\nVisible evidence.\n\nUser message:\nExplain using only this passage."}],
        "source": {"text": "UNRELATED FULL PAPER"}, "previous_candidate": {"content": "Unsupported old claim"}}
    messages = native_cot_messages("Solve the task.", task, "Check assumptions.")
    assert messages[1:] == task["prompt"]
    assert len(messages) == 2 and "<revision_context>" in messages[0]["content"]
    assert "UNRELATED FULL PAPER" not in messages[0]["content"]
    assert task["previous_candidate"]["content"] == "Unsupported old claim"
    state = native_execution_state({"native_environment_version": 1}, task, compile_task_contract(task))
    assert state["source_scope"] == "learner_excerpt_only" and state["assistant_history"] == "absent"
    scoped = native_cot_messages("Solve the task.", {**task, NATIVE_EXECUTION_STATE: state}, "Check assumptions.")
    assert scoped[0]["content"].split("\n<native_execution_state>", 1)[0] == messages[0]["content"]
    assert scoped[1:] == messages[1:]


def test_unsupported_tool_loop_is_not_silently_ignored():
    settings = config()
    settings["cot"]["source_lookup"] = {}
    with pytest.raises(ValueError):
        validate_node_generation(settings)


def test_native_execution_leak_is_evidence_not_an_edit_or_a_keyword_ban():
    from lib.contexts.workflows.domain.native_cot import native_instruction_leakage
    text = 'Need follow revision feedback. The system says to output in Chinese.'
    evidence = native_instruction_leakage(text, [{"role": "user", "content": "Explain the method."}])
    assert len(evidence) == 2
    assert all(text[e['start']:e['end']] == e['quote'] for e in evidence)
    assert native_instruction_leakage('The system prompt sets the assistant behavior.',
        [{"role": "user", "content": "Explain what a system prompt does."}]) == []
    state_leak = "I will obey native_execution_state before answering."
    visible = [{"role": "user", "content": "Explain the method."}]
    assert native_instruction_leakage(state_leak, visible) == []
    assert native_instruction_leakage(state_leak, visible, environment_version=1)[0]["field"] == "reasoning"
    assert native_instruction_leakage(state_leak, visible, environment_version=1, field="answer")[0]["field"] == "answer"
