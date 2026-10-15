"""Offline contracts for private knowledge, visible reading and language dispatch."""
from copy import deepcopy

import pytest

from lib.domain.workflow_qa_director import (
    DEFAULT_TYPE_WEIGHTS, QA_TYPES, learning_goal_types, scheduled_user_language,
    validate_director_task, validate_dialogue_step, validate_qa_director, validate_question_brief,
)
from lib.infrastructure.workflow_qa_director import WorkflowQADirector, learner_prompt


SOURCE = "Disconnect power before inspecting equipment."


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """Every model below is an in-process stub; no HTTP or external models."""
    yield


def config(**changes):
    return validate_qa_director({"enabled": True, "planning_mode": "adaptive",
        "batch_size": 2, "history_limit": 0, **changes})


def task(kind="closed_book", **changes):
    return {"id": "one", "qa_type": kind, "question": "What must I do before inspection?",
        "visible_context": "" if kind == "closed_book" else SOURCE,
        "answer_policy": "answer", "guidance": "Apply the supported safety precondition.",
        "evidence_quotes": [SOURCE], "dialogue_design": {"interaction_goal": "Plan a safe inspection."},
        **changes}


def test_new_controls_are_opt_in_validated_and_copy_language_preferences():
    old = config()
    assert "learning_goal" not in old and "user_languages" not in old
    supplied = ["en", "zh", "en"]
    normalized = config(learning_goal="knowledge_application", user_languages=supplied)
    assert normalized["user_languages"] == ["en", "zh"]
    normalized["user_languages"].append("en")
    assert supplied == ["en", "zh", "en"]
    assert "user_languages" not in config(learning_goal="mixed")
    assert "learning_goal" not in config(user_languages=["en"])
    assert config(learning_goal="knowledge_application", planning_mode="balanced",
                  type_weights=dict.fromkeys(QA_TYPES, 0))["learning_goal"] == "knowledge_application"


@pytest.mark.parametrize("changes", [
    {"learning_goal": "knowledge"}, {"learning_goal": None}, {"learning_goal": []},
    {"user_languages": []}, {"user_languages": "en"}, {"user_languages": ("zh",)},
    {"user_languages": ["de"]}, {"user_languages": ["zh", 1]}, {"user_languages": ["ZH"]},
    {"learning_goal": "document_reading", "planning_mode": "balanced",
     "type_weights": {kind: int(kind == "closed_book") for kind in QA_TYPES}},
])
def test_invalid_controls_fail_before_dispatch(changes):
    with pytest.raises(ValueError, match="invalid_qa_director"):
        config(**changes)


def test_private_knowledge_is_closed_book_with_authentic_evidence_not_a_visible_article():
    original = task()
    saved = deepcopy(original)
    result = validate_director_task(original, expected_type=None, source_text=SOURCE,
        learning_goal="knowledge_application", user_language="en")
    assert learner_prompt(result) == original["question"]
    assert result["visible_context"] == "" and result["evidence_quotes"] == [SOURCE]
    assert result["learning_goal"] == "knowledge_application" and result["user_language"] == "en"
    assert original == saved
    with pytest.raises(ValueError, match="qa_director_learning_goal_mismatch"):
        validate_director_task(task("grounded"), expected_type=None, learning_goal="knowledge_application")
    with pytest.raises(ValueError, match="qa_director_closed_book_contract"):
        validate_director_task(task(visible_context=SOURCE), expected_type=None,
                               learning_goal="knowledge_application")
    with pytest.raises(ValueError, match="qa_director_evidence_not_in_source"):
        validate_director_task(task(evidence_quotes=["Invented knowledge."]), expected_type=None,
                               source_text=SOURCE, learning_goal="knowledge_application")


def test_reading_keeps_actual_source_and_mixed_does_not_set_a_type_quota():
    for kind in QA_TYPES:
        value = task(kind)
        if kind == "multi_source":
            value["evidence_quotes"] = [SOURCE, "Disconnect power"]
        result = validate_director_task(value, expected_type=None, source_text=SOURCE, learning_goal="mixed")
        assert result["qa_type"] == kind
    with pytest.raises(ValueError, match="qa_director_learning_goal_mismatch"):
        validate_director_task(task(), expected_type=None, learning_goal="document_reading")
    with pytest.raises(ValueError, match="qa_director_visible_evidence_required"):
        validate_director_task(task("partial", visible_context=""), expected_type=None,
                               learning_goal="document_reading")
    reading = validate_director_task(task("grounded"), expected_type=None,
        source_text=SOURCE, learning_goal="document_reading")
    assert learner_prompt(reading).startswith("资料：\n" + SOURCE)
    english = validate_director_task(task("grounded"), expected_type=None,
        source_text=SOURCE, learning_goal="document_reading", user_language="en")
    assert learner_prompt(english) == "Source material:\n" + SOURCE + "\n\nUser message:\n" + english["question"]
    assert learning_goal_types("mixed") == QA_TYPES


def test_language_is_a_stable_whole_dialogue_contract_not_character_detection():
    assert [scheduled_user_language(["en", "zh"], i) for i in range(5)] == ["en", "zh", "en", "zh", "en"]
    assert scheduled_user_language(["en", "zh"], 7) == "zh"
    with pytest.raises(ValueError, match="language_offset"):
        scheduled_user_language(["zh"], -1)
    # Transport never translates a user's words or pretends to certify them;
    # the semantic question-fit/review model sees both words and declaration.
    original = task(question="先做什么？")
    result = validate_director_task(original, expected_type=None, user_language="en")
    assert result["question"] == "先做什么？" and original.get("user_language") is None
    with pytest.raises(ValueError, match="user_language_mismatch"):
        validate_director_task(task(user_language="zh"), expected_type=None, user_language="en")
    assert validate_question_brief({"topic": "Equipment inspection", "communication_goal": "Ask for help.",
                                    "user_language": "en"})["user_language"] == "en"


class Planner(WorkflowQADirector):
    """Exercise actual request freezing, validators and all dispatch boundaries."""
    def __init__(self, tmp_path, options, *, attempted=0, chosen="grounded"):
        self.recipe = {"version": 21, "qa_director": config(**options), "node_prompt_system": "System rules.",
                       "brief": "Apply reliable knowledge.", "conversation_turns": 3}
        self.state = {"production": {"attempted": attempted}, "stages": {"director": {
            key: 0 for key in ("done", "outputs", "eligible", "quarantined", "batches_done")}},
            "qa_director": {"coverage": {kind: dict.fromkeys(("planned", "assigned", "accepted", "rejected"), 0)
                                        for kind in QA_TYPES}, "feedback": {}, "batches_planned": 0}}
        self.path, self.calls, self.events, self.cached, self.chosen = tmp_path, [], [], {}, chosen
    def save(self, **kwargs):
        pass
    def activate_directed_stage(self, stage):
        self.stage = stage
    def shared_source_cleaning(self):
        return False
    def production_enabled(self):
        return False
    def prompt_text(self, *args, **kwargs):
        return "Editable stage instructions."
    def event(self, name, **fields):
        self.events.append((name, fields))
    def checkpoint(self, key, action):
        identity = repr(key)
        if identity not in self.cached:
            self.cached[identity] = deepcopy(action())
        return deepcopy(self.cached[identity])
    def _assess_question_brief(self, unit, contract, brief, visible_messages, *, key):
        # The detailed semantic checker is outside this transport unit test.
        self.brief_checked = deepcopy(brief)
    def ask(self, key, role, prompt_id, data, **kwargs):
        self.calls.append((prompt_id, deepcopy(data), role))
        if data.get("request_phase") == "next_turn":
            return {"continue": True, "user_message": "Can you make the plan shorter?",
                "reason": "Revise the actual previous output.", "dialogue_state": {"progress": "Draft given."},
                "question_brief": {"topic": "Equipment inspection", "communication_goal": "Request a shorter plan."}}
        if prompt_id == "workflow.question_user":
            return {"message": "What should I do before inspecting equipment?"}
        if prompt_id == "workflow.question_fit":
            return {"keep": True, "answer_policy": "answer", "reason": "Question is supported and in English."}
        tasks = []
        for candidate in data["candidates"]:
            kind = candidate["assigned_type"] or self.chosen
            item = task(kind, id=candidate["id"])
            if candidate.get("human_design"):
                item["question"] = candidate["human_design"]["seed"]["question"]
                # Deliberately false automatic labels must not stick to fixed
                # human text when an automatic default is enabled in the UI.
                item.update(learning_goal="knowledge_application", user_language="en")
            if self.separated_questions() and kind == "closed_book":
                item["question_brief"] = {"topic": "Equipment inspection", "communication_goal": "Ask for safety guidance."}
            tasks.append(item)
        return {"tasks": tasks}


def units(count):
    return [{"id": "one" if i == 0 else "two", "source_id": "source", "text": SOURCE, "kind": "document"}
            for i in range(count)]


def test_real_planner_request_constrains_knowledge_and_resumes_the_same_language_assignment(tmp_path):
    worker = Planner(tmp_path, {"learning_goal": "knowledge_application", "user_languages": ["zh", "en"]}, attempted=3)
    rows = worker._director_batch_plan(units(2), 0, worker.recipe["qa_director"], None, None, ["sft", "multiturn"])
    data = worker.calls[0][1]
    assert data["assigned_types"] == ["closed_book", "closed_book"]
    assert data["evidence_modes"] == ["closed_book"]
    assert [c["user_language"] for c in data["candidates"]] == ["en", "zh"]
    assert [r["qa_contract"]["user_language"] for r in rows] == ["en", "zh"]
    assert all(learner_prompt(r["qa_contract"]) == r["qa_contract"]["question"] for r in rows)
    assert all(r["text"] == SOURCE and r["qa_contract"]["evidence_quotes"] == [SOURCE] for r in rows)
    before = len(worker.calls)
    assert worker._director_batch_plan(units(2), 0, worker.recipe["qa_director"], None, None, ["sft", "multiturn"]) == rows
    assert len(worker.calls) == before


def test_omitted_controls_leave_adaptive_request_unassigned_and_without_new_fields(tmp_path):
    worker = Planner(tmp_path, {})
    worker._director_batch_plan(units(1), 0, worker.recipe["qa_director"], None, None, ["sft"])
    data = worker.calls[0][1]
    assert data["assigned_types"] == [None] and data["evidence_modes"] == list(DEFAULT_TYPE_WEIGHTS)
    assert not {"learning_goal", "user_languages", "automatic_controls_preserve_fixed_human_input"} & data.keys()
    assert not {"learning_goal", "user_language", "automatic_controls_applied"} & data["candidates"][0].keys()


def test_reading_rejects_planner_switch_to_closed_book_and_mixed_records_actual_choice(tmp_path):
    reading = Planner(tmp_path, {"learning_goal": "document_reading"}, chosen="closed_book")
    rows = reading._director_batch_plan(units(1), 0, reading.recipe["qa_director"], None, None, ["sft"])
    assert rows[0]["director_plan_failure"] == "qa_director_plan_invalid_after_repair"
    assert len(reading.calls) == 3
    mixed = Planner(tmp_path, {"learning_goal": "mixed"}, chosen="partial")
    rows = mixed._director_batch_plan(units(1), 0, mixed.recipe["qa_director"], None, None, ["sft"])
    assert rows[0]["qa_contract"]["qa_type"] == "partial"
    assert mixed.state["qa_director"]["coverage"]["partial"]["planned"] == 1
    assert all(mixed.state["qa_director"]["coverage"][kind]["planned"] == 0 for kind in QA_TYPES if kind != "partial")


def test_balanced_type_weights_cannot_override_the_selected_learning_objective(tmp_path):
    # A weighted closed-book-only preset cannot turn a reading task into a
    # hidden-article task, while knowledge application remains source-private.
    knowledge = Planner(tmp_path, {"learning_goal": "knowledge_application", "planning_mode": "balanced",
                                   "type_weights": dict.fromkeys(QA_TYPES, 0) | {"grounded": 100}})
    result = knowledge._director_batch_plan(units(1), 0, knowledge.recipe["qa_director"], None, None, ["sft"])
    assert result[0]["qa_contract"]["qa_type"] == "closed_book"
    knowledge.recipe["qa_director"]["type_weights"] = dict.fromkeys(QA_TYPES, 0)
    result = knowledge._director_batch_plan(units(1), 1, knowledge.recipe["qa_director"], None, None, ["sft"])
    assert result[0]["qa_contract"]["qa_type"] == "closed_book"
    reading = Planner(tmp_path, {"learning_goal": "document_reading", "planning_mode": "balanced"})
    result = reading._director_batch_plan(units(2), 0, reading.recipe["qa_director"], None, None, ["sft"])
    assert all(row["qa_contract"]["qa_type"] != "closed_book" for row in result)
    assert all(row["qa_contract"]["visible_context"] for row in result)


def test_fixed_human_question_and_context_are_preserved_without_false_language_tags(tmp_path):
    human = {"enabled": True, "seeds": [{"question": "请按我贴的说明核对这项操作。", "answer": "先断电。"}]}
    worker = Planner(tmp_path, {"learning_goal": "knowledge_application", "user_languages": ["en"],
                                "human_augmentation": human}, chosen="grounded")
    rows = worker._director_batch_plan(units(1), 0, worker.recipe["qa_director"], None, None, ["sft"])
    candidate = worker.calls[0][1]["candidates"][0]
    assert candidate["preserve_human_input"] and not candidate["automatic_controls_applied"]
    assert candidate["assigned_type"] is None and "user_language" not in candidate
    contract = rows[0]["qa_contract"]
    assert contract["question"] == human["seeds"][0]["question"] and contract["visible_context"] == SOURCE
    assert "user_language" not in contract and "learning_goal" not in contract
    assert any(name == "human_learning_controls_preserved" for name, _ in worker.events)


def test_fixed_human_closed_book_question_does_not_get_reauthored_by_blind_actor(tmp_path):
    human = {"enabled": True, "seeds": [{"question": "我这条问题请保持原文。", "answer": "先断电。"}]}
    worker = Planner(tmp_path, {"question_separation": True, "learning_goal": "knowledge_application",
        "user_languages": ["en"], "human_augmentation": human}, chosen="closed_book")
    rows = worker._director_batch_plan(units(1), 0, worker.recipe["qa_director"], None, None, ["sft"])
    assert rows[0]["qa_contract"]["question"] == human["seeds"][0]["question"]
    assert not any(prompt == "workflow.question_user" for prompt, _, _ in worker.calls)
    assert "user_language" not in rows[0]["qa_contract"]


def test_blind_questioner_fit_and_followup_share_language_without_receiving_teacher_text(tmp_path):
    worker = Planner(tmp_path, {"question_separation": True, "learning_goal": "knowledge_application",
                                "user_languages": ["en"]})
    rows = worker._director_batch_plan(units(1), 0, worker.recipe["qa_director"], None, None, ["multiturn"])
    unit = rows[0]
    actor = next(data for prompt, data, _ in worker.calls if prompt == "workflow.question_user")
    fit = next(data for prompt, data, _ in worker.calls if prompt == "workflow.question_fit")
    assert actor["question_brief"]["user_language"] == "en" and SOURCE not in repr(actor)
    assert fit["user_language"] == "en" and fit["learning_goal"] == "knowledge_application"
    history = [{"role": "user", "content": unit["qa_contract"]["question"]},
               {"role": "assistant", "content": "Disconnect power first."}]
    step = worker.next_dialogue_step(unit, history, {})
    assert step["user_language"] == "en" and step["learning_goal"] == "knowledge_application"
    followups = [data for prompt, data, _ in worker.calls if prompt == "workflow.question_user"]
    assert len(followups) == 2 and followups[-1]["question_brief"]["user_language"] == "en"
    assert followups[-1]["visible_dialogue"] == history
    wrong = {"continue": True, "user_message": "请简短一点。", "reason": "Revise draft.",
             "dialogue_state": {}, "user_language": "zh"}
    with pytest.raises(ValueError, match="user_language_mismatch"):
        validate_dialogue_step(wrong, completed_turns=1, user_language="en")
