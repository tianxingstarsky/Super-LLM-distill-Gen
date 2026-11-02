"""Actual profile transport, natural stopping and immutable old context rules."""
from copy import deepcopy
from hashlib import sha256
import json

import pytest

from lib.domain.dialogue_expression import carry_expression_design, natural_expression_guide
from lib.domain.workflow_quality import canonical
from lib.infrastructure.dialogue_role_messages import question_role_messages
from lib.prompts import base


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


PROFILE = {"participant_voice": "直接与助手交流，不替第三人称出题。", "register": "简洁日常口语",
           "syntax": "允许省略主语与自我修正", "noise": "可不使用噪声",
           "form": "简短请求", "clarity": "有未知条件", "verbosity": "很短"}


def test_numeric_prompt_latest_does_not_rewrite_an_exact_version_pin(monkeypatch):
    old = base.PromptSpec("test.prompt", "1.9.0", "old", "test", "old immutable")
    new = base.PromptSpec("test.prompt", "1.10.0", "new", "test", "new immutable")
    monkeypatch.setattr(base, "registry_versions", lambda: {old.id: {old.version: old, new.version: new}})
    assert base.get(old.id) is new and base.registry()[old.id] is new
    assert base.get(old.id, "1.9.0") is old


def test_legacy_guide_and_new_source_free_guide_are_version_pinned():
    assert sha256(canonical(natural_expression_guide()).encode()).hexdigest() == (
        "ffdfa8666177e78b6a331fe6b94b76a0f551658e7a5e31f55b2a33c5ebc28216")
    brief = {"topic": "普通求助", "communication_goal": "表达当前困惑", "expression_design": PROFILE}
    data = {"question_brief": brief, "natural_expression_guidance": natural_expression_guide(2)}
    with pytest.raises(ValueError, match="private_context_in_question_request"):
        question_role_messages("system", data)
    actual = question_role_messages("system", data, expression_guide_version=2)
    assert "participant_voice" in actual[0]["content"]
    assert not any(message.get("name") == "source_memory" for message in actual)
    mutated = {**data, "natural_expression_guidance": {**data["natural_expression_guidance"], "source_fact": "hidden"}}
    with pytest.raises(ValueError, match="private_context_in_question_request"):
        question_role_messages("system", mutated, expression_guide_version=2)


def test_only_compatible_speaker_habits_carry_to_another_turn():
    copied = deepcopy(PROFILE)
    next_turn = carry_expression_design(PROFILE, {"form": "修改请求", "verbosity": "说明新增条件"})
    assert next_turn["register"] == PROFILE["register"]
    assert next_turn["form"] == "修改请求" and next_turn["verbosity"] == "说明新增条件"
    assert "clarity" not in next_turn and PROFILE == copied


def test_style_contexts_are_opt_in_siblings_and_constraint_projection_preserves_them():
    from lib.domain.workflow_qa_director import validate_director_task, validate_question_brief
    from lib.contexts.workflows.contracts import validate_referenced_dialogue_step
    source = "设备检查先断电。"
    task = {"id": "one", "qa_type": "closed_book", "question": "检查前先做什么？",
        "visible_context": "", "answer_policy": "answer", "guidance": "保留实际前置条件。",
        "evidence_quotes": [source], "expression_design": {"form": "直接请求"},
        "dialogue_contexts": ["iterative", "direct"]}
    with pytest.raises(ValueError, match="invalid_qa_director_task"):
        validate_director_task(task, expected_type=None, source_text=source)
    selected = validate_director_task(task, expected_type=None, source_text=source,
        dialogue_style_context_version=1)
    assert selected["dialogue_contexts"] == ["direct", "iterative"]
    assert selected["expression_design"] == task["expression_design"]
    default_task = {key: value for key, value in task.items() if key != "dialogue_contexts"}
    assert "dialogue_contexts" not in validate_director_task(default_task, expected_type=None)
    assert validate_director_task(default_task, expected_type=None,
        dialogue_style_context_version=1)["dialogue_contexts"] == []
    brief = {"topic": "设备检查", "communication_goal": "表达本轮疑问", "dialogue_contexts": ["exploratory"]}
    with pytest.raises(ValueError, match="invalid_question_brief"):
        validate_question_brief(brief)
    assert validate_question_brief(brief, dialogue_style_context_version=1)["dialogue_contexts"] == ["exploratory"]
    messages = [{"role": "user", "content": "先处理检查记录。"}, {"role": "assistant", "content": "已经列好。"}]
    step = {"continue": True, "user_message": "记录项呢？", "reason": "承接已经列出的内容。",
        "user_constraint_refs": [0], "dialogue_state": {"dialogue_contexts": ["iterative"]}}
    with pytest.raises(ValueError, match="invalid_dialogue_step"):
        validate_referenced_dialogue_step(step, messages=messages, completed_turns=1, natural_utterance=True)
    normalized = validate_referenced_dialogue_step(step, messages=messages, completed_turns=1,
        natural_utterance=True, dialogue_style_context_version=1)
    assert normalized["dialogue_state"]["dialogue_contexts"] == ["iterative"]
    assert normalized["dialogue_state"]["active_constraints"] == [messages[0]["content"]]
    step["dialogue_state"].pop("dialogue_contexts")
    assert validate_referenced_dialogue_step(step, messages=messages, completed_turns=1,
        natural_utterance=True, dialogue_style_context_version=1)["dialogue_state"]["dialogue_contexts"] == []


@pytest.mark.parametrize("new_policy", [False, True])
@pytest.mark.parametrize("continue_dialogue", [False, True])
def test_director_counts_actual_answers_with_public_notes_in_request_key_and_validation(new_policy, continue_dialogue):
    from lib.bootstrap.dialogue_style_contexts import dialogue_style_context_catalog
    from lib.contexts.workflows.domain.dialogue import DialoguePlanError
    from lib.contexts.workflows.domain.dialogue_style_context import context_system_message
    from lib.infrastructure.workflow_qa_director import WorkflowQADirector

    class CounterProbe(WorkflowQADirector):
        def __init__(self):
            self.calls = []
            self.recipe = {"conversation_turns": 2, "dialogue_style_context_version": 1,
                "dialogue_style_context_catalog": dialogue_style_context_catalog(),
                "qa_director": {"question_rules": "Follow actual user input.", "answer_rules": "Use actual evidence."}}
            if new_policy:
                self.recipe["multiturn_advisory_review_version"] = 1

        def separated_questions(self, *args):
            return False

        def ask(self, key, role, prompt_id, payload, **options):
            self.calls.append((deepcopy(key), deepcopy(payload)))
            return {"continue": continue_dialogue, "user_message": "Update the record." if continue_dialogue else "",
                "reason": "Respond to the actual progress.", "dialogue_state": {"dialogue_contexts": ["iterative"]}}

        def _invalidate_director_call(self, key, **options):
            pass

    probe = CounterProbe()
    note, _ = context_system_message(probe.recipe, ["direct"])
    messages = [{"role": "system", "content": "Actual public conversation rules."},
        {"role": "user", "content": "Prepare an inspection record."}, note,
        {"role": "assistant", "content": "The record contains the preparation and findings."}]
    unit = {"id": "saved-task", "kind": "document", "text": "Original authenticated source.",
        "qa_contract": {"question": messages[1]["content"], "qa_type": "grounded", "answer_policy": "answer"}}
    original = deepcopy(messages)
    if new_policy and not continue_dialogue:
        with pytest.raises(DialoguePlanError):
            probe.next_dialogue_step(unit, messages, {})
    else:
        step = probe.next_dialogue_step(unit, messages, {})
        assert step["continue"] is continue_dialogue
    key, sent = probe.calls[0]
    assert len(probe.calls) == 1 and messages == original
    assert key[2] == sent["completed_turns"] == (1 if new_policy else 2)
    assert sent["maximum_turns"] == 2 and sent["dialogue_messages"] == messages


def test_style_context_prompt_requires_new_snapshot_flag_and_preserves_literal_overrides():
    from lib.domain.workflow_node_prompts import snapshot_node_prompts
    from lib.prompts import get, render
    from lib.prompts.workflow_planning import QA_DIRECTOR_V1_15_SPEC
    old = snapshot_node_prompts(None, recipe_version=42)
    new = snapshot_node_prompts(None, recipe_version=42, dialogue_style_context_version=1)
    assert old["director"]["workflow.qa_director"] == render(QA_DIRECTOR_V1_15_SPEC)
    assert new["director"]["workflow.qa_director"] == render(get("workflow.qa_director", "1.16.0"))
    assert old["director"]["workflow.qa_director"] != new["director"]["workflow.qa_director"]
    custom = '用户自定义原样保留：{"dialogue_contexts":"由本轮选择"}'
    supplied = {"director": {"workflow.qa_director": custom}}
    assert snapshot_node_prompts(supplied, recipe_version=42,
        dialogue_style_context_version=1)["director"]["workflow.qa_director"] == custom


def test_top_level_design_reaches_the_real_blind_actor_without_private_evidence(tmp_path):
    from tests.test_directed_answer_contracts import fit_workflow, question_dispatch
    workflow, aware, actor, _, unit = fit_workflow(tmp_path, source=True)
    unit["qa_contract"]["expression_design"] = deepcopy(PROFILE)
    before = deepcopy(unit)
    result = workflow._closed_book_question(unit, unit["qa_contract"], key=["expression-profile"])
    assert question_dispatch(actor.calls[0])["question_brief"]["expression_design"] == PROFILE
    assert result["expression_design"] == PROFILE and unit == before
    assert len(actor.calls) == len(aware.fit_requests) == 1
    assert "UNSELECTED_PRIVATE_V2" not in json.dumps(actor.calls, ensure_ascii=False)


def test_current_style_context_reaches_blind_actor_and_does_not_inherit_previous_selection(tmp_path):
    from lib.bootstrap.dialogue_style_contexts import dialogue_style_context_catalog
    from tests.test_directed_answer_contracts import fit_workflow, question_dispatch
    workflow, aware, actor, _, unit = fit_workflow(tmp_path, source=True)
    workflow.recipe["dialogue_style_context_version"] = 1
    workflow.recipe["dialogue_style_context_catalog"] = dialogue_style_context_catalog()
    unit["qa_contract"]["dialogue_contexts"] = ["narrative"]
    original = deepcopy(unit)
    initial = workflow._closed_book_question(unit, unit["qa_contract"], key=["style-context"])
    next_turn = workflow._closed_book_question(unit, initial, key=["style-context"], dialogue_contexts=[])
    assert initial["dialogue_contexts"] == ["narrative"] and next_turn["dialogue_contexts"] == []
    assert [question_dispatch(call)["question_brief"]["dialogue_contexts"] for call in actor.calls] == [["narrative"], []]
    assert len(actor.calls) == len(aware.fit_requests) == 2 and unit == original
    assert "UNSELECTED_PRIVATE_V2" not in json.dumps(actor.calls, ensure_ascii=False)
    catalog = workflow.recipe["dialogue_style_context_catalog"]["contexts"]
    first_system, next_system = actor.calls[0][0]["content"], actor.calls[1][0]["content"]
    assert catalog["narrative"]["description"] in first_system
    assert catalog["narrative"]["description"] not in next_system
    assert all(item["content"] not in first_system for item in catalog.values())
    assert all(item["description"] not in first_system for kind, item in catalog.items() if kind != "narrative")


@pytest.mark.parametrize("malformed_contexts", [False, True])
def test_multiturn_keeps_speaker_habits_and_transmits_the_current_turn_design(tmp_path, malformed_contexts):
    from lib.bootstrap.dialogue_style_contexts import dialogue_style_context_catalog
    from lib.infrastructure import training_workflow as engine
    from lib.io_utils import atomic_json
    from lib.prompts import get, render
    from tests.test_separated_dialogue_generation import (
        DirectorResponder, Questioner, make_run, records, task_payload, tool_payload, brief,
    )
    # This is a transport fixture, not a natural-language quality judgment.
    # The same speaker expands a concern, then uses a short contextual follow-up.
    utterances = [
        "把设备检查的准备和收尾整理成一张操作卡。",
        "我想把这张卡贴在工作台旁边。现在这些说明我能看懂，不过操作时未必有空逐句读，"
        "最好把准备动作和收尾记录分开，让人一眼能找到当前要做什么。",
        "记录项呢？",
    ]
    current_designs = [
        {**PROFILE, "register": "日常口语", "interaction_style": "直接提出当前请求"},
        {"form": "叙述自己的使用顾虑", "verbosity": "展开当前场景与感受",
         "interaction_style": "从实际卡片解释不方便的地方"},
        {"form": "承接卡片的省略追问", "verbosity": "只提当前遗漏",
         "interaction_style": "询问刚才的记录项"},
    ]
    current_contexts = [["direct"], ["narrative", "iterative"], []]
    expected_contexts = [[], [], []] if malformed_contexts else current_contexts

    class ProfileDirector(DirectorResponder):
        def chat(self, messages, **options):
            data = task_payload(messages)
            dispatch = tool_payload(messages, "dialogue_dispatch")
            if data and data.get("request_phase") == "next_turn":
                self.calls.append(deepcopy(messages))
                self.steps.append(deepcopy(messages))
                completed = data["completed_turns"]
                keep_going = completed < len(utterances)
                result = {"continue": keep_going, "user_message": "" if not keep_going else "内部占位",
                    "reason": "按已完成的卡片决定是否仍有需要表达的顾虑。",
                    "dialogue_state": {"progress": "卡片已提供，用户据此决定下一行动。"}}
                if data.get("dialogue_constraint_provenance_version") == 1:
                    result["user_constraint_refs"] = list(data["user_message_indices"])
                if keep_going:
                    result["dialogue_state"]["expression_design"] = current_designs[completed]
                    if current_contexts[completed]:
                        result["dialogue_state"]["dialogue_contexts"] = (
                            ["not-a-style"] if malformed_contexts else current_contexts[completed])
                    result["question_brief"] = brief(followup=True)
                return json.dumps(result, ensure_ascii=False)
            if data and "question_brief" in data and "question" in data and "qa_contract" in data:
                self.calls.append(deepcopy(messages))
                self.fits.append(deepcopy(messages))
                assert data["question"] in utterances
                return json.dumps({"keep": True, "answer_policy": "answer", "reason": "回应实际卡片请求。"})
            if dispatch is not None and "qa_type" in dispatch:
                self.calls.append(deepcopy(messages))
                self.answers.append(deepcopy(messages))
                assert messages[-1]["content"] in utterances
                return json.dumps({"answer": "准备区先断电；操作区检查设备；收尾区记录检查结果。",
                    "reasoning": "PRIVATE_REASONING_SENTINEL 必须保留准备和收尾条件。",
                    "quotes": [self.source_quote]}, ensure_ascii=False)
            reply = json.loads(super().chat(messages, **options))
            if "tasks" in reply:
                for task in reply["tasks"]:
                    task["expression_design"] = deepcopy(current_designs[0])
                    task["dialogue_contexts"] = ["not-a-style"] if malformed_contexts else current_contexts[0]
            return json.dumps(reply, ensure_ascii=False)

    class ContextualQuestioner(Questioner):
        def chat(self, messages, **options):
            self.calls.append(deepcopy(messages))
            assert tool_payload(messages, "source_memory") is None
            dispatch = tool_payload(messages, "dialogue_dispatch")
            turn_index = len(dispatch.get("visible_dialogue", [])) // 2
            assert dispatch["question_brief"]["expression_design"]["verbosity"] == current_designs[turn_index]["verbosity"]
            assert dispatch["question_brief"]["dialogue_contexts"] == expected_contexts[turn_index]
            return json.dumps({"message": utterances[turn_index]}, ensure_ascii=False)

    director = ProfileDirector()
    worker, _, actor, _ = make_run(tmp_path, writer=director, questioner=ContextualQuestioner(),
                                  targets=["multiturn"], conversation_turns=4)
    # The product enables this protocol for native generation. This existing
    # role-transport fixture freezes it explicitly without changing its author.
    worker.recipe.update(dialogue_style_context_version=1,
        dialogue_style_context_catalog=dialogue_style_context_catalog())
    worker.recipe["node_prompt_templates"]["director"]["workflow.qa_director"] = render(get("workflow.qa_director", "1.16.0"))
    worker.state["recipe_hash"] = engine.digest(worker.recipe)
    atomic_json(worker.path / "recipe.json", worker.recipe)
    worker.save()
    state = worker.execute()
    assert state["status"] == "completed", state.get("error")
    actual_designs = [tool_payload(call, "dialogue_dispatch")["question_brief"]["expression_design"]
                      for call in actor.calls]
    assert actual_designs[0] == current_designs[0]
    for index, design in enumerate(actual_designs[1:], 1):
        assert design["register"] == current_designs[0]["register"]
        assert design["verbosity"] == current_designs[index]["verbosity"]
        assert design["interaction_style"] == current_designs[index]["interaction_style"]
        assert "clarity" not in design
    assert len(actor.calls) == len(director.answers) == len(director.fits) == 3
    assert len(director.plans) == 1 and len(director.steps) == 3
    for call in [*director.plans, *director.steps]:
        request = task_payload(call)
        assert request["dialogue_style_context_version"] == 1
        assert all(set(item) == {"id", "label", "description"}
                   for item in request["dialogue_style_context_catalog"]["contexts"])
    assert "PRIVATE_REASONING_SENTINEL" not in json.dumps(actor.calls, ensure_ascii=False)
    row, = records(worker.path, "multiturn")
    trainer = (worker.path / "artifacts/trl_multiturn.jsonl").read_text(encoding="utf-8")
    assert "expression_design" not in trainer and PROFILE["syntax"] not in trainer
    assert [message["content"] for message in row["messages"] if message["role"] == "user"] == utterances
    assert [step["dialogue_state"]["expression_design"] for step in row["dialogue_steps"]
            if step.get("continue")] == actual_designs
    assert row["qa_contract"]["dialogue_contexts"] == expected_contexts[0]
    assert [step["dialogue_state"]["dialogue_contexts"] for step in row["dialogue_steps"][1:]
            if step.get("continue")] == expected_contexts[1:]
    if malformed_contexts:
        receipt = row["qa_contract"]["dialogue_context_selection"]
        assert receipt["status"] == "invalid_selection_fallback" and receipt["fallback"] == "common"
        assert receipt["fields"] == ["dialogue_contexts"]
        assert row["dialogue_steps"][1]["dialogue_context_selection"]["status"] == "invalid_selection_fallback"
        assert any("not-a-style" in path.read_text(encoding="utf-8")
                   for path in (worker.path / "checkpoints").rglob("*.json"))
    # The initial plan precedes any actual user turn; only later step requests
    # can cite already delivered user messages.
    assert row["dialogue_steps"][0]["dialogue_state"]["active_constraints"] == []
    assert len(row["dialogue_steps"]) == len(director.steps) + 1
    for call, step in zip(director.steps, row["dialogue_steps"][1:]):
        request = task_payload(call)
        indices = request["user_message_indices"]
        assert step["dialogue_state"]["active_constraints"] == [
            request["dialogue_messages"][index]["content"] for index in indices]
        assert all(request["dialogue_messages"][index]["role"] == "user" for index in indices)
        assert step["constraint_provenance"]["semantic_status"] == "not_certified"


@pytest.mark.parametrize("custom", [False, True])
def test_user_expression_upgrade_keeps_frozen_node_bodies_and_literal_custom_rules(tmp_path, monkeypatch, custom):
    from lib.infrastructure import training_workflow as engine
    from lib.prompts import get, render
    frozen = {
        "director": {"workflow.qa_director": render(get("workflow.qa_director", "1.11.0")),
                     "workflow.question_user": render(get("workflow.question_user", "1.3.0"))},
        "multiturn": {"workflow.multiturn_user": render(get("workflow.multiturn_user", "1.5.0"))},
    }
    if custom:
        frozen = {stage: {prompt_id: '我的规则保持原样：{"voice":"由场景决定"} ' + prompt_id
                          for prompt_id in prompts} for stage, prompts in frozen.items()}
    output = tmp_path / "out"
    run_id = engine.create_run(output, brief="共同完善一份操作卡。", targets=["multiturn"], node_prompts=frozen)
    workflow = engine.Workflow(output, run_id, tmp_path)

    def registry_unavailable(*args, **kwargs):
        raise AssertionError("Frozen node bodies must not consult the upgraded registry")

    monkeypatch.setattr(engine, "get", registry_unavailable)
    for stage, prompts in frozen.items():
        for prompt_id, body in prompts.items():
            assert workflow.prompt_text(prompt_id, stage=stage) == body


def test_persistent_expression_window_is_bounded_without_erasing_ledger_entries(tmp_path):
    from tests.test_workflow_knowledge_agent import Worker, Client
    from lib.domain.dialogue_expression import EXPRESSION_FIELDS_V2
    worker = Worker(tmp_path, Client([]))
    worker.recipe["qa_expression_version"] = 2
    for index in range(15):
        worker.knowledge_store().claim("knowledge", str(index), run_id="saved", sample_id=str(index),
            question="问题" + str(index), qa_type="closed_book",
            expression_design={field: field + " " + str(index) + "x" * 210 for field in EXPRESSION_FIELDS_V2})
        worker.knowledge_store().outcome("knowledge", str(index), run_id="saved", sample_id=str(index),
            stage="sft", status="final_eligible")
    bounded = worker._knowledge_memory("knowledge")
    assert len(canonical(bounded).encode()) <= 8192 and bounded["expression_coverage_limited"]
    full = worker.knowledge_store().coverage("knowledge", include_expression=True)
    assert full["counts"] == [{"qa_type": "closed_book", "status": "final_eligible", "count": 15}]
    assert sum(map(len, full["accepted_dimensions"].values())) > sum(map(len, bounded["accepted_dimensions"].values()))
