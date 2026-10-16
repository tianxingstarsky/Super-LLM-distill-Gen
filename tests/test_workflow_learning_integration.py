"""Exercise language/learning controls through real offline workflow envelopes."""
from copy import deepcopy
import json

import pytest
from streamlit.testing.v1 import AppTest

from lib.bootstrap.prompt_library import prompt_library_application
from lib.domain.learner_context import learner_contract
from lib.domain.workflow_generation import reasoning_language_instruction, style_for_sample
from lib.domain.workflow_node_prompts import _prompt_catalog, active_node_prompt_ids
from lib.domain.workflow_quality import accepted, canonical
from lib.infrastructure import training_workflow as engine
from lib.infrastructure.workflow_node_messages import split_node_rules
from tests.test_workflow_prompt_library import library_key, workbench
from tests.test_workflow_styled_generation import model_task_data
from tests.test_turn_review_and_reasoning_repair import grade, reviewed


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """Every model in these tests is an injected local Python object."""
    yield


SOURCE = "Inspection requires power to be disconnected first. Record the results after inspection."
FIRST = "What should I do before inspecting the equipment?"
SECOND = "And after the inspection?"
REASONS = (
    "断电是检查的必要前提；the inspection must wait until power is disconnected，不能颠倒这个依赖。",
    "检查结果在检查完成后才存在；recording follows inspection，因此最后记录结果，不凭空填写发现。",
)
ANSWERS = ("Disconnect the power before starting the inspection.", "Record the results after the inspection.")
CONFIG = {"enabled": True, "question_separation": True, "learning_goal": "knowledge_application",
          "user_languages": ["en"]}
STYLE = {"enabled": True, "style": "concise", "reasoning_language": "zh_en"}


class OfflineClient:
    """Return fixed content while observing the actual production requests."""
    model = "offline-learning-integration"

    def __init__(self, *, missing_reasoning=False):
        self.calls, self.kwargs, self.usage = [], [], {"calls": 0}
        self.answer_count = 0
        self.missing_reasoning = missing_reasoning

    def chat(self, messages, **kwargs):
        self.calls.append(deepcopy(messages))
        self.kwargs.append(dict(kwargs))
        self.usage["calls"] += 1
        data = model_task_data(messages)
        system = messages[0]["content"]
        if data.get("supervised_assistant_turns") is not None:
            value = reviewed(data["candidate"])
        elif "context_complete" in system and "leakage_free" in system:
            value = {"keep": True, "context_complete": True, "leakage_free": True,
                     "reason": "The actual dialogue is self-contained; reasoning language is independent."}
        elif data.get("request_phase") == "next_turn":
            value = {"continue": True, "user_message": SECOND, "reason": "Continue with the remaining operation.",
                     "dialogue_state": {"progress": "Preparation has been explained."},
                     "question_brief": {"topic": "Equipment inspection", "communication_goal": "Ask what to do afterward."}}
        elif "adherence" in system:
            value = {"keep": True, "adherence": 5, "reason": "The actual task, English reply, and mixed explanation agree."}
        elif "question_brief" in data and "qa_contract" in data:
            value = ({"keep": True, "answer_policy": "answer", "reason": "The independent question is supported."}
                     if "question" in data else {"keep": True, "reason": "The brief contains no private facts."})
        elif "question_brief" in data:
            value = {"message": SECOND}
        elif "context" in data and "answer" in data:
            value = grade()
        elif "qa_type" in data and "answer_policy" in data and ("prompt" in data or "messages" in data):
            index = min(self.answer_count, 1)
            self.answer_count += 1
            value = {"answer": ANSWERS[index], "quotes": [SOURCE]}
            if not self.missing_reasoning:
                value["reasoning"] = REASONS[index]
        else:
            raise AssertionError(f"Unexpected offline request fields: {sorted(data)}")
        return json.dumps(value, ensure_ascii=False)


def prepare(tmp_path, *, review=False, style_node="multiturn", separated=True, missing_reasoning=False):
    output = tmp_path / "output"
    options = {"review_repair": {"mode": "auto", "max_rounds": 1}} if review else {}
    run_id = engine.create_run(output, brief="Apply equipment inspection knowledge.", targets=["multiturn"],
        conversation_turns=2, node_generation={style_node: STYLE},
        qa_director={**CONFIG, "question_separation": separated}, **options)
    # These fixed callback envelopes exercise the earlier language/learning
    # contracts. Independent claim and anchor review have their own real-wire
    # integration tests; do not accidentally feed them an aggregate-only mock.
    if review:
        from lib.io_utils import atomic_json
        from lib.prompts import get, render
        path = engine.run_path(output, run_id)
        recipe = engine.read_json(path / "recipe.json")
        for name in tuple(recipe):
            if name.startswith("claim_review_") or name in {
                    "claim_refutation_release_version", "claim_reference_policy_version"}:
                recipe.pop(name)
        prompt = get("workflow.review_score", version="1.11.0")
        recipe["prompts"][prompt.id] = {"version": prompt.version, "sha256": engine.digest(prompt.template)}
        recipe["node_prompt_templates"]["review"].pop("workflow.review_reference", None)
        recipe["node_prompt_templates"]["review"][prompt.id] = render(prompt)
        atomic_json(path / "recipe.json", recipe)
        state = engine.read_json(path / "state.json")
        state["recipe_hash"] = engine.digest(recipe)
        atomic_json(path / "state.json", state)
    client = OfflineClient(missing_reasoning=missing_reasoning)
    worker = engine.Workflow(output, run_id, tmp_path, generator=client, jev=client, questioner=client)
    worker.stage = "multiturn"
    unit = {"id": "sample", "source_id": "manual", "source_name": "PRIVATE_SOURCE_NAME",
            "family_id": "family", "kind": "document", "text": SOURCE,
            "qa_contract": {"qa_type": "closed_book", "question": FIRST, "visible_context": "",
                "answer_policy": "answer", "guidance": "Explain the required operation.",
                "evidence_quotes": [SOURCE], "learning_goal": "knowledge_application", "user_language": "en"}}
    return worker, client, unit


@pytest.mark.parametrize("already_pinned", [False, True])
def test_pinned_style_has_exactly_one_language_instruction(tmp_path, already_pinned):
    worker, _, _ = prepare(tmp_path)
    expected = style_for_sample(STYLE, "sample", reasoning_language_version=3)
    rule = reasoning_language_instruction("zh_en", version=3)
    pinned = expected["instruction"] if already_pinned else "A versioned custom explanation rule."
    worker.recipe["node_generation_prompts"]["multiturn"]["concise"] = pinned
    actual = worker.generation_style("multiturn", "sample")
    assert actual["instruction"].count(rule) == 1
    assert actual["instruction"] == (pinned if already_pinned else pinned + "\n" + rule)
    assert actual["reasoning_language"] == "zh_en"


@pytest.mark.parametrize("data", [
    {"source": {"text": "PRIVATE_DOCUMENT"}, "task": "Clean this text."},
    {"text": "PRIVATE_DOCUMENT", "provenance": {"kind": "document"}},
    {"source": {"qa_contract": {"user_language": "en"}, "text": "PRIVATE_DOCUMENT"}},
])
def test_cpt_and_parser_envelopes_receive_no_dialogue_controls(data):
    original = deepcopy(data)
    suffix, task = split_node_rules(data, qa_director=CONFIG)
    assert suffix == "" and canonical(task) == canonical(original)
    assert data == original


def test_omitted_controls_preserve_the_exact_legacy_system_suffix_and_payload():
    data = {"qa_contract": {"qa_type": "closed_book"}, "source": {"text": SOURCE},
            "question_rules": "QUESTION_RULE", "answer_rules": "ANSWER_RULE"}
    original = deepcopy(data)
    suffix, task = split_node_rules(data, instruction="NODE_RULE", qa_director={
        "enabled": True, "question_rules": "QUESTION_RULE", "answer_rules": "ANSWER_RULE"})
    expected = ("\n本节点固定处理规则（node_task_instructions）：\nNODE_RULE"
                "\n本节点用户配置的提问规则（question_rules）：\nQUESTION_RULE"
                "\n本节点用户配置的回答规则（answer_rules）：\nANSWER_RULE")
    assert suffix.encode("utf-8") == expected.encode("utf-8")
    assert canonical(task) == canonical({"qa_contract": data["qa_contract"], "source": data["source"]})
    assert data == original


@pytest.mark.parametrize("version", range(9, 22))
def test_historical_multiturn_catalog_keeps_its_original_exact_ids(version):
    original = ("workflow.multiturn_user", "workflow.multiturn_assistant", "workflow.jev_score", "workflow.multiturn_consistency")
    if version > 10:
        original += ("workflow.multiturn_directed_check",)
    if version >= 17:
        original += ("workflow.human_augmentation_check",)
    assert canonical(_prompt_catalog(version)["multiturn"]).encode("utf-8") == canonical(original).encode("utf-8")
    assert "workflow.style_check" not in _prompt_catalog(version)["multiturn"]


def test_new_multiturn_style_prompt_pin_is_versioned_and_only_offered_when_enabled():
    assert _prompt_catalog(22)["multiturn"] == (*_prompt_catalog(21)["multiturn"], "workflow.style_check")
    assert "workflow.style_check" in active_node_prompt_ids("multiturn", "文档资料", node_generation={"multiturn": STYLE})
    assert "workflow.style_check" in active_node_prompt_ids("multiturn", "文档资料", node_generation={"sft": STYLE})
    assert "workflow.style_check" not in active_node_prompt_ids("multiturn", "文档资料", node_generation={
        "multiturn": {"enabled": False}, "sft": STYLE})


def test_version_22_passes_real_execute_guard_and_resume_with_offline_clients(tmp_path):
    messages = [{"role": "user", "content": FIRST},
                {"role": "assistant", "content": ANSWERS[0], "reasoning_content": REASONS[0]},
                {"role": "user", "content": SECOND},
                {"role": "assistant", "content": ANSWERS[1], "reasoning_content": REASONS[1]}]
    source = tmp_path / "recorded.jsonl"
    source.write_text(json.dumps({"messages": messages}, ensure_ascii=False) + "\n", encoding="utf-8")
    output = tmp_path / "output"
    run_id = engine.create_run(output, sources=[source], targets=["multiturn"], node_generation={"multiturn": STYLE})
    path = engine.run_path(output, run_id)
    recipe = engine.read_json(path / "recipe.json")
    assert recipe["version"] == 22
    assert "workflow.style_check" in recipe["node_prompt_templates"]["multiturn"]

    class MalformedFirstReview(OfflineClient):
        def chat(self, messages, **kwargs):
            super().chat(messages, **kwargs)
            return json.dumps({"keep": "invalid"})

    failed_client = MalformedFirstReview()
    state = engine.Workflow(output, run_id, tmp_path, generator=failed_client, jev=failed_client).execute()
    assert state["status"] == "failed" and state["error"] == "invalid_judge_boolean"
    assert len(failed_client.calls) == 1  # The execution/version guard was passed.
    client = OfflineClient()
    restored = engine.resume(output, run_id, tmp_path, generator=client, jev=client)
    assert restored["status"] == "completed", restored.get("error")
    assert restored["attempt"] == 2
    assert engine.verify_artifacts(path)["counts"] == {"multiturn": 1}
    assert json.loads((path / "artifacts/multiturn.jsonl").read_text(encoding="utf-8"))["messages"] == messages
    assert client.answer_count == 0  # Imported text is never rewritten by the new style.
    assert engine.read_json(path / "recipe.json") == recipe


def test_manual_input_takes_priority_over_automatic_goal_and_language():
    data = {"qa_contract": {"human_design": {"seed": {"question": "请保持我贴出的中文原文。"}},
                            "question": "请保持我贴出的中文原文。", "visible_context": SOURCE}}
    suffix, actual = split_node_rules(data, qa_director=CONFIG)
    assert "不翻译或改写固定人工输入" in suffix
    assert "本段对话用户主要使用英文" not in suffix
    assert "不在训练用户输入中附加教师原文" not in suffix
    assert actual == data


def test_english_user_and_mixed_reasoning_are_independent_rules():
    data = {"qa_contract": {"qa_type": "closed_book", "learning_goal": "knowledge_application", "user_language": "en"},
            "generation_style": style_for_sample(STYLE, "sample")}
    suffix, task = split_node_rules(data, qa_director=CONFIG)
    assert "本段对话用户主要使用英文" in suffix
    assert "助手最终回答跟随用户语言" in suffix
    assert "不能因推理与答案语言不同而直接判错" in suffix
    assert task["generation_style"]["reasoning_language"] == "zh_en"
    assert task["generation_style"]["instruction"].endswith(reasoning_language_instruction("zh_en"))


def test_blind_question_call_has_language_but_no_teacher_material(tmp_path):
    worker, client, _ = prepare(tmp_path)
    data = {"question_brief": {"topic": "Equipment inspection", "communication_goal": "Ask what to do afterward.",
                                "user_language": "en"},
            "question_rules": worker.recipe["qa_director"]["question_rules"]}
    value = worker.ask(["blind"], "question", "workflow.question_user", data,
                       model_stage="director", prompt_stage="director")
    assert value == {"message": SECOND}
    messages = client.calls[0]
    assert "本段对话用户主要使用英文" in messages[0]["content"]
    assert SOURCE not in canonical(messages) and "PRIVATE_SOURCE_NAME" not in canonical(messages)
    assert not any(message.get("name") == "source_memory" for message in messages)
    dispatch = model_task_data(messages)
    assert dispatch["question_brief"]["user_language"] == "en"
    assert not {"qa_contract", "source_context", "teacher_evidence", "evidence_quotes"} & dispatch.keys()


@pytest.mark.parametrize("review", [False, True])
@pytest.mark.parametrize("style_node", ["multiturn", "sft"])
def test_actual_two_turn_generation_keeps_mixed_reasoning_and_provenance_in_every_turn(tmp_path, review, style_node):
    worker, client, unit = prepare(tmp_path, review=review, style_node=style_node)
    row, = worker.multiturn(unit)
    assert row["status"] == "eligible", row
    assert row["messages"] == [{"role": "user", "content": FIRST},
        {"role": "assistant", "content": ANSWERS[0], "reasoning_content": REASONS[0]},
        {"role": "user", "content": SECOND},
        {"role": "assistant", "content": ANSWERS[1], "reasoning_content": REASONS[1]}]
    assert row["generation_style"]["reasoning_language"] == "zh_en"
    assert row["reasoning_origin"] == "prompt_styled_generation"
    assert [turn["reasoning_origin"] for turn in row["turn_reviews"]] == ["prompt_styled_generation"] * 2
    answers = [messages for messages in client.calls if messages[-1] in (
        {"role": "user", "content": FIRST}, {"role": "user", "content": SECOND})]
    assert len(answers) == 2
    for messages in answers:
        dispatch = model_task_data(messages)
        assert dispatch["learning_goal"] == "knowledge_application" and dispatch["user_language"] == "en"
        assert dispatch["generation_style"] == row["generation_style"]
        assert "本段对话用户主要使用英文" in messages[0]["content"]
        assert reasoning_language_instruction("zh_en", version=3) in messages[0]["content"]
        assert SOURCE not in canonical([m for m in messages if m["role"] == "user"])
    style_calls = [messages for messages in client.calls if "你是风格与表达审校员" in messages[0]["content"]]
    assert len(style_calls) == (0 if review else 2)
    if review:
        assert row["source_context"]["qa_contract"]["user_language"] == "en"
        worker.stage = "review"
        check = worker._review_score("multiturn", row,
            {"teacher_evidence": SOURCE, "source_kind": "document", "source_id": "manual"}, ["sample", "review"], "")
        assert accepted(check)
        review_data = [model_task_data(messages) for messages in client.calls
                       if "supervised_assistant_turns" in model_task_data(messages)]
        assert len(review_data) == 1
        assert review_data[0]["generation_style"] == row["generation_style"]
        assert review_data[0]["qa_contract"]["user_language"] == "en"
        assert [turn["message_index"] for turn in review_data[0]["supervised_assistant_turns"]] == [1, 3]
        assert learner_contract(row["qa_contract"]) == {"qa_type": "closed_book", "answer_policy": "answer",
            "learning_goal": "knowledge_application", "user_language": "en"}


def test_style_requires_real_nonempty_reasoning_before_turn_is_accepted(tmp_path):
    worker, client, unit = prepare(tmp_path, missing_reasoning=True)
    row, = worker.multiturn(unit)
    assert row["status"] == "quarantined" and row["failed_turn"] == 1
    assert "必须返回非空reasoning" in row["feedback"]
    assert client.answer_count == 2
    assert row["turn_reviews"] == []
    assert "reasoning_origin" not in row


def test_multiturn_does_not_apply_new_style_to_imported_conversation(tmp_path):
    worker, client, unit = prepare(tmp_path)
    messages = [{"role": "user", "content": FIRST}, {"role": "assistant", "content": ANSWERS[0], "reasoning_content": "Keep this literal native trace."},
                {"role": "user", "content": SECOND}, {"role": "assistant", "content": ANSWERS[1]}]
    unit.update(kind="conversation", messages=deepcopy(messages))
    unit.pop("qa_contract")
    row, = worker.multiturn(unit)
    assert row["status"] == "eligible" and row["messages"] == messages
    assert "generation_style" not in row and "reasoning_origin" not in row
    assert client.answer_count == 0


def test_missing_multiturn_flag_preserves_literal_previous_generation_path(tmp_path):
    worker, client, unit = prepare(tmp_path, style_node="sft")
    worker.recipe.pop("multiturn_generation_style_version", None)
    old, = worker._multiturn(unit)
    old_request_bytes = canonical(client.calls)
    old_count = len(client.calls)
    replayed, = worker.multiturn(unit)
    assert old["status"] == "eligible"
    assert canonical(replayed) == canonical(old)
    assert canonical(client.calls) == old_request_bytes and len(client.calls) == old_count
    assert "generation_style" not in replayed and "reasoning_origin" not in replayed
    assert not any("generation_style" in model_task_data(call) for call in client.calls)


def test_multiturn_only_node_can_save_language_and_restore_its_own_template(tmp_path):
    ui = workbench(tmp_path)
    ui.pills(key="workflow-targets:fixture:自动推荐").set_value(["multiturn"]).run()
    ui.button(key="fixture-node:multiturn").click().run()
    enabled = "workflow-generation-enabled:fixture:multiturn"
    key = "workflow-generation-reasoning-language:fixture:multiturn"
    assert ui.selectbox(key=key).disabled
    ui.toggle(key=enabled).set_value(True).run()
    ui.selectbox(key=key).set_value("zh_en").run()
    scope = "generation:multiturn"
    ui.text_input(key=library_key(scope, "name")).set_value("Multi-turn mixed reasoning").run()
    ui.button(key=library_key(scope, "save")).click().run()
    library = prompt_library_application(root=tmp_path)
    saved, = library.list_templates(scope)
    assert library.get_template(saved["id"], scope)["payload"]["reasoning_language"] == "zh_en"
    ui.button(key=library_key(scope, "restore")).click().run()
    assert ui.selectbox(key=key).value == "auto" and ui.toggle(key=enabled).value is False
    ui.selectbox(key=library_key(scope, "selection")).set_value(saved["id"]).run()
    assert ui.selectbox(key=key).value == "zh_en" and ui.toggle(key=enabled).value is True
    assert not ui.exception
