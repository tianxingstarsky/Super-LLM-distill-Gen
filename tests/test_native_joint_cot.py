"""Real workflow boundary: native channels, source scope, restart and coverage."""
from copy import deepcopy
import json
import pytest

from lib.infrastructure import training_workflow as engine
from lib.bootstrap.workflow_execution import workflow_execution
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


@pytest.mark.parametrize("capture_author", [True, False])
@pytest.mark.parametrize("canonical_control", [False, True])
def test_new_sft_native_uses_its_frozen_prompt_and_resumes_one_real_request_boundary(tmp_path, request, capture_author, canonical_control):
    text = "设备维护前必须断电；然后检查线路，完成维护后记录实际结果。"
    source = tmp_path / "manual.txt"
    source.write_text(text, encoding="utf8")
    output = tmp_path / "output"
    generation = {"sft": {"enabled": True, "style": "default", "answer_mode": "native_joint",
        "instruction": "", "self_check": default_integrated_reflection()}}
    rid = engine.create_run(output, sources=[source], targets=["sft", "cot"], node_generation=generation,
                            package_review={"enabled": False})
    path = engine.run_path(output, rid)
    frozen = engine.read_json(path / "recipe.json")
    assert frozen["native_author_session_version"] == 1
    assert frozen["native_context_contract_version"] == 1
    if not capture_author:
        # Historical queued tasks did not record an author-session envelope.
        frozen.pop("native_author_session_version")
        state = engine.read_json(path / "state.json")
        state["recipe_hash"] = engine.digest(frozen)
        atomic_json(path / "recipe.json", frozen)
        atomic_json(path / "state.json", state)
    unit = {"id": "directed-01", "source_id": "manual", "family_id": "manual-task",
        "kind": "document", "text": text, "qa_contract": {
            "id": "directed-01", "qa_type": "closed_book", "guidance": "解释维护的安全前提。",
            "question": "维护时先做什么？", "visible_context": "", "answer_policy": "answer", "evidence_quotes": [text]}}
    sdk = None
    wire = []
    if canonical_control:
        import httpx
        from openai import OpenAI
        from lib.llm_client import ChatClient
        def respond(http_request):
            assert http_request.url.host == "offline.invalid"
            wire.append(json.loads(http_request.content))
            return httpx.Response(200, json={"id": "offline-native", "object": "chat.completion", "created": 0,
                "model": "offline-author", "choices": [{"index": 0, "finish_reason": "stop", "message": {
                    "role": "assistant", "content": "先断电，再检查线路。",
                    "reasoning_content": "  用户问维护的先后。带电检查有风险，必须先断电再检查线路。\n"}}]})
        sdk = ChatClient("https://offline.invalid/v1", "offline-test-key", "offline-author", reasoning={
            "schema": "shujian.reasoning-effort/v1", "mode": "enabled", "effort": "high",
            "provider_mapping": {level: {"mode": "enabled", "effort": "low"}
                for level in ("low", "medium", "high", "xhigh", "max")}})
        sdk.client.close()
        sdk.client = OpenAI(base_url="https://offline.invalid/v1", api_key="offline-test-key", max_retries=0,
            http_client=httpx.Client(transport=httpx.MockTransport(respond)))
        sdk.stream_supported = False
        request.addfinalizer(sdk.client.close)
    class Author:
        calls = 0
        reasoning = sdk.reasoning if sdk else {"mode": "enabled"}
        def chat(self, messages, **options):
            self.calls += 1
            self.last_messages = deepcopy(messages)
            assert messages[-1] == {"role": "user", "content": unit["qa_contract"]["question"]}
            assert text in messages[0]["content"]
            mode = json.loads(messages[0]["content"].split("<native_context_contract>\n", 1)[1]
                .split("\n</native_context_contract>", 1)[0])
            assert mode["qa_type"] == "closed_book" and mode["teacher_source_visibility"] == "private"
            assert mode["learner_excerpt_only"] is False
            assert not options.get("json_output") and options["retries"] == 1
            if sdk:
                return sdk.chat(messages, **options)
            options["response_details"].update(api_format="chat",
                reasoning_content="  用户问维护的先后。带电检查有风险，必须先断电再检查线路。\n")
            return "先断电，再检查线路。"
    author = Author()
    def offline_reviews(worker):
        worker.judge_answer = lambda *args, **options: {"keep": True, "grounded": True,
            "reasoning_valid": True, "correctness": 5,
            "scores": {key: 5 for key in ("correctness", "reasoning", "grounding", "instruction", "safety")},
            "reason": "已知测试输入中的安全前提与回答一致。"}
        worker.qa_contract_check = lambda *args, **options: {"keep": True, "adherence": 5, "reason": "回答实际问题。"}
        worker.style_check = lambda *args, **options: {"keep": True, "adherence": 5, "reason": "保留实际判断。"}
    workflow = engine.Workflow(output, rid, tmp_path, generator=author)
    offline_reviews(workflow)
    workflow.stage = "sft"
    assert workflow.recipe["native_generation_version"] == 1
    assert workflow.recipe["version"] == 42
    # Exercise the actual composition root before the node: direct calls to
    # sft() alone cannot catch a new recipe rejected at the execution boundary.
    session = workflow_execution(workflow)._session
    with session.exclusive():
        session.verify_pinned_inputs()
    assert author.calls == 0
    row, = workflow.sft(unit)
    assert row["status"] == "eligible" and row["reasoning_origin"] == "provider_reasoning"
    assert row["messages"][-1]["reasoning_content"].startswith("  用户问维护的先后。")
    assert row["reasoning_call"]["stage"] == "sft"
    if canonical_control:
        entry, = row["generation_controls"]["assistant_turns"]
        assert entry["message_index"] == len(row["messages"]) - 1
        assert entry["generation_control"]["effort"] == "high"
        assert entry["generation_control"]["provider"]["effort"] == "low"
        assert wire[0]["reasoning_effort"] == "low"
        assert "schema" not in wire[0] and "generation_control" not in wire[0]
        assert entry["reasoning_call"] == row["reasoning_call"]
    else:
        assert "generation_control" not in row and "generation_controls" not in row
    assert ("author_session" in row["native_generation"]) == capture_author
    if capture_author:
        assert row["native_generation"]["author_session"]["messages"] == author.last_messages
    assert "author_session" not in json.dumps(training_record("sft", row), ensure_ascii=False)
    assert row["provided_source_context"]["semantic_support"] == "not_assessed"
    resumed = engine.Workflow(output, rid, tmp_path, generator=author)
    offline_reviews(resumed)
    resumed.stage = "sft"
    assert resumed.sft(unit) == [row] and author.calls == 1
    from lib.contexts.workflows.infrastructure.execution_runtime import CheckpointedBatchRuntime
    collections = {"sft": [row], "cot": []}
    adapter = CheckpointedBatchRuntime(resumed)
    adapter.finalize_reasoning(collections)
    assert not isinstance(collections["cot"], list)  # Lazy export projection.
    atomic_json(resumed.path / "input_records.json", [{**unit, "status": "ready"}])
    adapter.publish(collections)
    sft = json.loads((resumed.path / "artifacts" / "sft.jsonl").read_text(encoding="utf8"))
    cot = json.loads((resumed.path / "artifacts" / "cot.jsonl").read_text(encoding="utf8"))
    assert cot["reasoning"] == [sft["messages"][-1]["reasoning_content"]]
    assert cot["answer"] == sft["messages"][-1]["content"] and author.calls == 1
    if canonical_control:
        expected = {str(len(row["messages"]) - 1): "high"}
        assert sft["training_control"]["by_message"] == expected
        assert cot["training_control"]["by_message"] == expected
    else:
        assert "training_control" not in sft and "training_control" not in cot


@pytest.mark.parametrize("invalid_recipe", ["changed_hash", "unsupported_version"])
def test_native_recipe_execution_boundary_still_rejects_changed_or_unknown_inputs(tmp_path, invalid_recipe):
    rid = engine.create_run(tmp_path, brief="维护安全问答", targets=["sft"],
        node_generation={"sft": {"enabled": True, "answer_mode": "native_joint"}})
    path = engine.run_path(tmp_path, rid)
    recipe = engine.read_json(path / "recipe.json")
    state = engine.read_json(path / "state.json")
    assert recipe["version"] == 42 and recipe["native_generation_version"] == 1
    if invalid_recipe == "changed_hash":
        recipe["brief"] = "运行创建后修改了任务要求。"
        assert engine.digest(recipe) != state["recipe_hash"]
    else:
        recipe["version"] = 10_000
        state["recipe_hash"] = engine.digest(recipe)
        atomic_json(path / "state.json", state)
    atomic_json(path / "recipe.json", recipe)
    session = workflow_execution(engine.Workflow(tmp_path, rid, tmp_path))._session
    with session.exclusive(), pytest.raises(ValueError, match="^recipe_changed_create_new_run$"):
        session.verify_pinned_inputs()


def test_new_reading_creation_uses_director_without_executing_archived_scene_prompt(tmp_path):
    source = tmp_path / "paper.md"
    source.write_text("实验结果只支持给定条件下的差异。", encoding="utf8")
    generation = {"multiturn": {"enabled": True, "style": "skeptical",
        "instruction": "保留适用条件。", "reading_session": True}}
    director = {"enabled": True, "planning_mode": "balanced", "learning_goal": "knowledge_application",
        "type_weights": {"closed_book": 100, "grounded": 0, "partial": 0, "multi_source": 0, "distractor": 0},
        "question_rules": "跟进真实的历史回应。", "answer_rules": "解释哪些证据仍然缺失。"}
    frozen_inputs = deepcopy((generation, director))
    rid = engine.create_run(tmp_path, sources=[source], targets=["multiturn"], node_generation=generation,
        qa_director=director, node_prompts={"multiturn": {"workflow.reading_scene": "Saved private scene guidance."}})
    recipe = engine.read_json(engine.run_path(tmp_path, rid) / "recipe.json")
    assert recipe["native_generation_version"] == 1
    assert recipe["node_generation"]["multiturn"]["answer_mode"] == "native_joint"
    assert recipe["node_generation"]["multiturn"]["style"] == "skeptical"
    assert not recipe["node_generation"]["multiturn"].get("reading_session")
    assert recipe["qa_director"]["learning_goal"] == "document_reading"
    assert recipe["qa_director"]["question_separation"] is True
    assert recipe["qa_director"]["type_weights"]["closed_book"] == 0
    assert recipe["qa_director"]["type_weights"]["grounded"] == 100
    assert recipe["qa_director"]["answer_rules"] == director["answer_rules"]
    assert recipe["reading_session_migration"]["archived_inactive_prompt_ids"] == ["workflow.reading_scene"]
    assert recipe["node_prompts"]["multiturn"]["workflow.reading_scene"] == "Saved private scene guidance."
    active = active_node_prompt_ids("multiturn", "文档资料", node_generation=recipe["node_generation"])
    assert "workflow.cot_native_joint" in active and "workflow.reading_scene" not in active
    assert (generation, director) == frozen_inputs


@pytest.mark.parametrize("cot_enabled", [True, False])
def test_new_native_sft_rejects_explicit_cot_stage_instead_of_silently_projecting(tmp_path, cot_enabled):
    generation = {"sft": {"enabled": True, "answer_mode": "native_joint"},
                  "cot": {"enabled": cot_enabled}}
    with pytest.raises(ValueError, match="^native_generation_conflicting_cot_stage$"):
        engine.create_run(tmp_path, brief="维护安全问答", targets=["sft", "cot"], node_generation=generation)
    assert not (tmp_path / "workflows").exists()


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
    # A native history stripped by the historical request path is not a
    # certified tool-continuation session, even for newly created tasks.
    assert "author_session" not in row["native_generation"]
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


def test_unmarked_native_source_request_preserves_its_literal_historical_projection():
    from lib.contexts.workflows.domain.dialogue import dialogue_digest
    from lib.contexts.workflows.domain.native_cot import native_context_contract
    task = {"prompt": [{"role": "user", "content": "What should be checked before maintenance?"}],
        "source": {"text": "Disconnect the power before checking the wiring."}}
    unchanged = deepcopy(task)
    assert native_context_contract({}, {}, task, compile_task_contract(task)) is None
    wire = native_cot_messages("Frozen template.", task, "Frozen instruction.")
    assert dialogue_digest(wire) == "4a3841ea043623938bd780ec5c099201ce9c4675441fb093399fca1719db9345"
    assert wire[1:] == task["prompt"] and task == unchanged


@pytest.mark.parametrize("goal,qa_type,voice", [
    ("knowledge_application", "closed_book", True),
    ("document_reading", "grounded", False),
    ("mixed", "closed_book", True),
    ("mixed", "grounded", False),
    (None, "closed_book", False),
])
def test_native_context_mode_uses_frozen_goal_and_contract_without_exposing_teacher_to_learner(goal, qa_type, voice):
    from lib.contexts.workflows.domain.native_cot import native_context_contract
    recipe = {"native_context_contract_version": 1, "qa_director": {"learning_goal": goal}}
    sample = {"learning_goal": "knowledge_application", "qa_contract": {"qa_type": qa_type,
        "learning_goal": "knowledge_application"}}
    task = {"prompt": [{"role": "user", "content": "Explain this maintenance decision."}],
        "source": {"text": "PRIVATE_AUTHENTICATED_TEACHER"}}
    original = deepcopy((recipe, sample, task))
    mode = native_context_contract(recipe, sample, task, compile_task_contract(task))
    assert mode == {"version": 1, "learning_goal": goal, "qa_type": qa_type,
        "teacher_source_visibility": "private", "learner_excerpt_only": False,
        "knowledge_application_voice": voice}
    custom = "Keep my literal {template}, {{braces}}, and role instructions.\n"
    wire = native_cot_messages(custom, {**task, "native_context_contract": mode}, "")
    assert wire[0]["content"].startswith(custom)
    assert json.loads(wire[0]["content"].split("<native_context_contract>\n", 1)[1]
        .split("\n</native_context_contract>", 1)[0]) == mode
    assert wire[1:] == task["prompt"]
    assert "PRIVATE_AUTHENTICATED_TEACHER" not in json.dumps(wire[1:])
    assert (recipe, sample, task) == original


def test_native_visible_excerpt_mode_keeps_reading_scope_and_excludes_private_full_source():
    from lib.contexts.workflows.domain.native_cot import native_context_contract
    from lib.contexts.workflows.domain.authored_task_contract import project_task_context
    recipe = {"native_context_contract_version": 1, "qa_director": {"learning_goal": "knowledge_application"}}
    task = {"prompt": [{"role": "user", "content": "Source material:\nVisible evidence.\n\nUser message:\nExplain using only this passage."}],
        "source": {"text": "UNRELATED PRIVATE FULL PAPER"}}
    contract = compile_task_contract(task)
    projected = project_task_context(task, contract)
    mode = native_context_contract(recipe, {"qa_contract": {"qa_type": "grounded"}}, projected, contract)
    assert mode["learner_excerpt_only"] is True and mode["knowledge_application_voice"] is False
    wire = native_cot_messages("Custom reading instructions.", {**projected, "native_context_contract": mode}, "")
    assert wire[1:] == task["prompt"]
    assert "UNRELATED PRIVATE FULL PAPER" not in json.dumps(wire)


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
