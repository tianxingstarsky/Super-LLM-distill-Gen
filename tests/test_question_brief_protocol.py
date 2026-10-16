"""Versioned brief limits and fatal-error boundaries, without model HTTP calls."""
from copy import deepcopy
import json

import pytest

from lib.domain.workflow_qa_director import (
    QuestionBriefValidationError, validate_dialogue_step, validate_director_task,
    validate_question_brief,
)
from lib.infrastructure.dialogue_role_messages import question_role_messages
from lib.infrastructure.workflow_qa_director import DialoguePlanError, WorkflowQADirector


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


# Actual lengths that exposed the old transport cap. Acceptance here tests
# schema only, not whether these instructions are grounded in a real history.
ENGLISH_BEHAVIORS = [
    "ask which of the previously listed published results may be placed beside the user's number and which may not",
    "state that their own model is roughly 11B parameters, the same range as the smaller published models listed",
]
MESSAGES = [{"role": "user", "content": "Help me arrange an operation card."},
            {"role": "assistant", "content": "Here is a first version of the card."}]


def brief(behaviors=None, **changes):
    return {"topic": "Operation card", "communication_goal": "Request a useful revision of the visible result.",
            "behaviors": list(ENGLISH_BEHAVIORS if behaviors is None else behaviors), **changes}


def step(brief_value=None):
    return {"continue": True, "user_message": "Please shorten the card.",
            "reason": "The user requests a revision of the visible result.",
            "dialogue_state": {"progress": "An initial card is available."},
            "question_brief": brief() if brief_value is None else brief_value}


def task(brief_value=None):
    return {"id": "sample", "qa_type": "closed_book", "question": "Internal design placeholder.",
            "visible_context": "", "answer_policy": "answer", "guidance": "Revise the visible result.",
            "evidence_quotes": [], "question_brief": brief() if brief_value is None else brief_value}


@pytest.mark.parametrize("behavior,length", list(zip(ENGLISH_BEHAVIORS, [109, 107])))
def test_new_brief_accepts_natural_english_descriptions_but_old_default_keeps_100(behavior, length):
    assert len(behavior) == length
    original = brief([behavior])
    frozen = deepcopy(original)
    assert validate_question_brief(original, version=2) == original
    with pytest.raises(ValueError, match="^invalid_question_brief$") as error:
        validate_question_brief(original)
    assert type(error.value) is ValueError and original == frozen
    assert validate_question_brief(brief(["x" * 100]))["behaviors"] == ["x" * 100]
    with pytest.raises(ValueError, match="invalid_question_brief"):
        validate_question_brief(brief(["x" * 101]), version=1)


@pytest.mark.parametrize("behaviors", [["x" * 513], [107], [None], [False], [""], ["  "],
                                       ["bad\x00text"], ["valid"] * 7, "not an array"])
def test_new_brief_still_rejects_invalid_behaviors_with_safe_field_limits(behaviors):
    value = brief(behaviors=[])
    value["behaviors"] = behaviors
    with pytest.raises(QuestionBriefValidationError) as error:
        validate_question_brief(value, version=2)
    assert str(error.value) == "invalid_question_brief"
    assert error.value.feedback == {"code": "invalid_question_brief", "field": "question_brief.behaviors",
        "max_items": 6, "item_max_chars": 512, "item_type": "nonempty_string"}
    assert "513" not in json.dumps(error.value.feedback)


def test_new_brief_boundary_512_and_six_items_are_accepted_without_mutation():
    value = brief(["x" * 512, "two", "three", "four", "five", "six"])
    frozen = deepcopy(value)
    result = validate_question_brief(value, version=2)
    assert result == value and value == frozen
    result["behaviors"].append("caller change")
    assert value == frozen


@pytest.mark.parametrize("field", ["private_source", "question_brief_protocol_version", "answer", "source_id"])
def test_brief_never_accepts_untrusted_extra_fields(field):
    with pytest.raises(QuestionBriefValidationError, match="invalid_question_brief"):
        validate_question_brief(brief(["request a revision"], **{field: "PRIVATE_SENTINEL"}), version=2)


@pytest.mark.parametrize("version", [None, True, False, "2", 2.0, 0, 3])
def test_malformed_protocol_versions_are_config_failures(version):
    with pytest.raises(ValueError, match="^invalid_question_brief_protocol_version$"):
        validate_question_brief(brief(), version=version)


def test_task_step_and_role_projection_use_trusted_keyword_not_actor_data():
    value = brief()
    assert validate_director_task(task(value), expected_type="closed_book",
        question_brief_protocol_version=2)["question_brief"] == value
    assert validate_dialogue_step(step(value), completed_turns=1,
        question_brief_protocol_version=2)["question_brief"] == value
    messages = question_role_messages("Speaker rules", {"question_brief": value}, question_brief_protocol_version=2)
    payload = json.loads(next(message["content"] for message in messages if message["role"] == "tool"))
    assert payload == {"question_brief": value}
    assert "question_brief_protocol_version" not in json.dumps(messages)
    for validate in (
        lambda: validate_director_task(task(value), expected_type="closed_book"),
        lambda: validate_dialogue_step(step(value), completed_turns=1),
        lambda: question_role_messages("Speaker rules", {"question_brief": value}),
    ):
        with pytest.raises(ValueError, match="invalid_question_brief"):
            validate()
    with pytest.raises(ValueError, match="private_context_in_question_request"):
        question_role_messages("Speaker rules", {"question_brief": value,
            "question_brief_protocol_version": 2}, question_brief_protocol_version=2)


class Probe(WorkflowQADirector):
    def __init__(self, *, version=2, value=None, fail_phase=None, failure=None):
        self.recipe = {"qa_expression_version": 2, "qa_director": {
            "question_separation": True, "question_rules": "Speak directly."}}
        if version is not None:
            self.recipe["question_brief_protocol_version"] = version
        self.value = step(brief(["request a revision"])) if value is None else value
        self.fail_phase, self.failure = fail_phase, failure
        self.calls, self.invalidated, self.committed, self.events = [], [], [], []

    def _director_evidence_materials(self, unit):
        return unit.get("materials", [])

    def ask(self, key, role, prompt_id, data, **options):
        self.calls.append((prompt_id, deepcopy(data)))
        if self.fail_phase == prompt_id:
            raise self.failure
        if prompt_id == "workflow.qa_director":
            return deepcopy(self.value)
        if prompt_id == "workflow.question_brief_check":
            return {"keep": True, "reason": "No private facts in this brief."}
        if prompt_id == "workflow.question_user":
            return {"message": "Please shorten the card."}
        if prompt_id == "workflow.question_fit":
            return {"keep": True, "answer_policy": "answer", "reason": "A useful revision."}
        raise AssertionError(prompt_id)

    def knowledge_agent_plan(self, key, data, runtime, **options):
        return self.ask(key, "generation", "workflow.qa_director", data, **options)

    def commit_knowledge_plan(self, key):
        if self.fail_phase == "commit":
            raise self.failure
        self.committed.append(key)

    def invalidate_knowledge_plan(self, key):
        self.invalidated.append(key)

    _invalidate_director_call = invalidate_knowledge_plan

    def event(self, *args, **kwargs):
        self.events.append((args, kwargs))

    def execute_step(self, *, runtime=True, unit=None):
        return self._next_dialogue_plan(["test"], {}, ("trusted",) if runtime else None,
            unit or {"qa_contract": task(brief(["request a revision"]))}, MESSAGES, {})


def test_new_step_validation_feedback_preserves_field_limit_without_private_content():
    worker = Probe(value=step(brief(["PRIVATE_SENTINEL" + "x" * 512])))
    with pytest.raises(DialoguePlanError) as error:
        worker.execute_step()
    assert error.value.code == "invalid_question_brief"
    assert error.value.feedback["field"] == "question_brief.behaviors"
    assert error.value.feedback["item_max_chars"] == 512
    assert "PRIVATE_SENTINEL" not in json.dumps(error.value.feedback)
    assert worker.invalidated == [["test"]] and not worker.committed
    assert [name for name, _ in worker.calls] == ["workflow.qa_director"]


def test_old_step_keeps_generic_error_and_legacy_limit():
    worker = Probe(version=None, value=step(brief()))
    with pytest.raises(DialoguePlanError, match="^invalid_dialogue_step$") as error:
        worker.execute_step()
    assert error.value.feedback == {"code": "invalid_dialogue_step"}
    assert worker.invalidated == [["test"]] and not worker.committed


@pytest.mark.parametrize("phase", ["workflow.qa_director", "workflow.question_brief_check",
                                  "workflow.question_user", "workflow.question_fit", "commit"])
@pytest.mark.parametrize("code", ["checkpoint_integrity_error", "invalid_source_memory_transport",
                                 "invalid_question_brief", "model_configuration_changed_create_new_run"])
def test_new_protocol_never_swallows_request_configuration_or_commit_errors(phase, code):
    failure = ValueError(code)
    worker = Probe(fail_phase=phase, failure=failure)
    with pytest.raises(ValueError) as error:
        worker.execute_step()
    assert error.value is failure and not isinstance(error.value, DialoguePlanError)
    assert not worker.invalidated and not worker.committed


def test_new_boundary_rejection_stops_before_actor_and_remains_safe_feedback():
    private = "PRIVATE_SOURCE_SENTINEL_WITH_FACT_197"
    worker = Probe(value=step(brief([private])))
    candidate = {"qa_contract": task(brief(["request a revision"])),
                 "materials": [{"material_id": "material", "text": private}]}
    with pytest.raises(DialoguePlanError) as error:
        worker.execute_step(unit=candidate)
    assert error.value.code == "question_brief_contains_private_source"
    assert error.value.feedback == {"code": "question_brief_contains_private_source"}
    assert not worker.committed and worker.invalidated == [["test"]]
    assert [name for name, _ in worker.calls] == ["workflow.qa_director"]


def test_new_factory_and_actual_three_hop_route_preserve_source_isolation(tmp_path):
    from tests.test_separated_dialogue_generation import DirectorResponder, make_run, records, tool_payload, SOURCE
    # Source-free language behavior, deliberately longer than the legacy cap.
    long_behavior = "ask for a shorter version of the visible operation card while keeping the useful preparation and completion details"
    initial = brief([long_behavior])
    followup = brief([long_behavior + " in the revision"])
    writer = DirectorResponder(initial_brief=initial, followup_brief=followup)
    worker, _, actor, _ = make_run(tmp_path, writer=writer, targets=["multiturn"], conversation_turns=4)
    assert worker.recipe["question_brief_protocol_version"] == 2
    state = worker.execute()
    assert state["status"] == "completed", state.get("error")
    assert len(actor.calls) == 2
    actual_briefs = [tool_payload(call, "dialogue_dispatch")["question_brief"] for call in actor.calls]
    assert actual_briefs[0]["behaviors"] == initial["behaviors"]
    assert actual_briefs[1]["behaviors"] == followup["behaviors"]
    body = json.dumps(actor.calls, ensure_ascii=False)
    assert SOURCE not in body and "source_memory" not in body and "question_brief_protocol_version" not in body
    row, = records(worker.path, "multiturn")
    assert len(row["messages"]) == 4


def test_real_multiturn_retry_receives_only_safe_field_limit_feedback(tmp_path):
    from tests.test_separated_dialogue_generation import DirectorResponder, make_run, records, task_payload

    class FirstInvalid(DirectorResponder):
        def chat(self, messages, **options):
            result = json.loads(super().chat(messages, **options))
            if result.get("continue") is True and len(self.steps) == 1:
                result["question_brief"]["behaviors"] = ["PRIVATE_BAD_BEHAVIOR" + "x" * 512]
            return json.dumps(result, ensure_ascii=False)

    writer = FirstInvalid()
    worker, _, actor, _ = make_run(tmp_path, writer=writer, targets=["multiturn"], conversation_turns=4)
    state = worker.execute()
    assert state["status"] == "completed", state.get("error")
    assert len(writer.steps) == 3  # Invalid continuation, valid continuation, natural end.
    feedback = task_payload(writer.steps[1])["feedback"]
    assert feedback == {"code": "invalid_question_brief", "field": "question_brief.behaviors",
        "max_items": 6, "item_max_chars": 512, "item_type": "nonempty_string"}
    assert "PRIVATE_BAD_BEHAVIOR" not in json.dumps(feedback)
    assert len(actor.calls) == 2  # No actor or answer call for the rejected step.
    row, = records(worker.path, "multiturn")
    assert len(row["messages"]) == 4 and row["status"] == "eligible"


@pytest.mark.parametrize("version", [None, True, "2", 2.0, 3])
def test_invalid_explicit_recipe_protocol_fails_before_a_plan_request(version):
    worker = Probe()
    worker.recipe["question_brief_protocol_version"] = version
    with pytest.raises(ValueError, match="invalid_question_brief_protocol_version") as error:
        worker.execute_step()
    assert not isinstance(error.value, DialoguePlanError)
    assert not worker.calls and not worker.invalidated and not worker.committed


@pytest.mark.parametrize("phase,code", [("commit", "invalid_qa_director_batch"),
                                       ("commit", "checkpoint_integrity_error"),
                                       ("actor", "invalid_question_brief")])
def test_new_initial_batch_does_not_reclassify_durable_commit_or_actor_request_failures(tmp_path, phase, code):
    from types import SimpleNamespace
    from lib.domain.workflow_qa_director import QA_TYPES, validate_qa_director
    from tests.test_dialogue_knowledge_agent import Worker, Client, final, unit

    config = validate_qa_director({"enabled": True, "context_mode": "persistent", "tools_mode": "auto",
        "batch_size": 1, "history_limit": 0, "question_separation": phase == "actor",
        "type_weights": {name: int(name == ("closed_book" if phase == "actor" else "grounded")) for name in QA_TYPES}})
    candidate = unit()
    valid = deepcopy(candidate["qa_contract"])
    valid["guidance"] = "Respond using the selected clean source."
    if phase == "actor":
        valid.update(qa_type="closed_book", visible_context="", question_brief=brief(["request a revision"]))
    worker = Worker(tmp_path, Client([final({"tasks": [valid]})]))
    worker.recipe.update(version=12, production={}, knowledge_agent_finalization_version=1,
                         question_brief_protocol_version=2)
    worker.recipe["qa_director"] = deepcopy(config)
    worker.state["stages"] = {"director": dict.fromkeys(
        ("done", "outputs", "quarantined", "eligible", "batches_done"), 0)}
    worker.state["qa_director"].update(coverage={name: {"planned": 0, "assigned": 0} for name in QA_TYPES},
                                       feedback={}, batches_planned=0)
    failure = ValueError(code)
    if phase == "commit":
        def fail_commit(key):
            raise failure
        worker.commit_knowledge_plan = fail_commit
    else:
        original_ask = worker.ask
        def fail_actor(key, role, prompt_id, data, **options):
            if prompt_id == "workflow.question_user":
                raise failure
            return original_ask(key, role, prompt_id, data, **options)
        worker.ask = fail_actor
    empty_history = SimpleNamespace(source_recent=lambda *args, **kwargs: [])
    with pytest.raises(ValueError) as error:
        worker._director_batch_plan([candidate], 0, config, empty_history, empty_history, ["multiturn"])
    assert error.value is failure and not worker.invalidated
    assert len(worker._client.requests) == 1
