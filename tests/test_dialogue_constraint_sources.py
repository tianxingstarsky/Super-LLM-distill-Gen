"""Replay a real assistant-to-user preference attribution without buying calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from lib.contexts.workflows.contracts import (
    DialogueConstraintReferenceError, validate_referenced_dialogue_step,
)
from lib.contexts.workflows.domain.dialogue import DialoguePlanError
from lib.infrastructure import training_workflow as engine
from lib.io_utils import atomic_json
from lib.prompts.workflow_planning import QA_DIRECTOR_V1_14_SPEC
from tests.test_separated_dialogue_generation import task_payload


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """Only one recorded/injected provider response is used by each adapter case."""
    yield


def recorded_case():
    return json.loads((Path(__file__).parent / "fixtures/reasoning_agent/dialogue_constraint_sources.json")
                     .read_text(encoding="utf-8"))


def referenced_response(fixture, refs):
    response = deepcopy(fixture["response"])
    response["dialogue_state"].pop("active_constraints")
    response["user_constraint_refs"] = refs
    return response


@pytest.mark.parametrize("mode", ["legacy", "user_source", "assistant_source"])
def test_actual_director_boundary_preserves_old_record_but_binds_new_constraints(tmp_path, mode):
    fixture = recorded_case()
    original = deepcopy(fixture)
    payload = fixture["payload"]
    response = (fixture["response"] if mode == "legacy"
                else referenced_response(fixture, [1 if mode == "user_source" else 2]))

    class RecordedDirector:
        model = "offline-recorded-director"

        def __init__(self):
            self.calls = []

        def chat(self, messages, **options):
            self.calls.append(deepcopy(messages))
            return json.dumps(response, ensure_ascii=False)

    author = RecordedDirector()
    output = tmp_path / "out"
    rid = engine.create_run(output, brief=payload["teacher_evidence"], targets=["multiturn"],
        qa_director={"enabled": True, "planning_mode": "adaptive", "question_separation": False})
    path = engine.run_path(output, rid)
    recipe, state = engine.read_json(path / "recipe.json"), engine.read_json(path / "state.json")
    if mode == "legacy":
        recipe.pop("dialogue_constraint_provenance_version", None)
        recipe["prompts"]["workflow.qa_director"] = {
            "version": QA_DIRECTOR_V1_14_SPEC.version, "sha256": engine.digest(QA_DIRECTOR_V1_14_SPEC.template)}
    else:
        recipe["dialogue_constraint_provenance_version"] = 1
    state["recipe_hash"] = engine.digest(recipe)
    atomic_json(path / "recipe.json", recipe)
    atomic_json(path / "state.json", state)
    worker = engine.Workflow(output, rid, tmp_path, generator=author)
    worker.stage = "multiturn"
    unit = {"id": "recorded-constraint-state", "source_id": "brief", "kind": "brief", "synthetic": True,
            "text": payload["teacher_evidence"], "qa_contract": deepcopy(payload["qa_contract"])}
    if mode == "assistant_source":
        with pytest.raises(DialoguePlanError, match="^invalid_dialogue_constraint_refs$") as rejected:
            worker.next_dialogue_step(unit, payload["dialogue_messages"], payload["previous_dialogue_state"])
        assert rejected.value.feedback["user_message_indices"] == [1]
    else:
        step = worker.next_dialogue_step(unit, payload["dialogue_messages"], payload["previous_dialogue_state"])
        if mode == "legacy":
            assert step["dialogue_state"]["active_constraints"] == fixture["response"]["dialogue_state"]["active_constraints"]
            assert "constraint_provenance" not in step
        else:
            assert step["dialogue_state"]["active_constraints"] == [payload["dialogue_messages"][1]["content"]]
            assert "不整段复述" not in step["dialogue_state"]["active_constraints"][0]
            assert step["constraint_provenance"]["semantic_status"] == "not_certified"
    request = task_payload(author.calls[0])
    assert request["dialogue_messages"] == payload["dialogue_messages"]
    assert request.get("user_message_indices") == (None if mode == "legacy" else [1])
    assert len(author.calls) == 1 and fixture == original


def test_real_user_acceptance_is_addressable_only_after_it_was_actually_said():
    fixture = recorded_case()
    messages = fixture["completed_messages"]
    selected = referenced_response(fixture, [7])
    with pytest.raises(DialogueConstraintReferenceError, match="^invalid_dialogue_constraint_refs$"):
        validate_referenced_dialogue_step(selected, messages=messages[:7], completed_turns=3, natural_utterance=True)
    step = validate_referenced_dialogue_step(selected, messages=messages, completed_turns=4, natural_utterance=True)
    assert step["dialogue_state"]["active_constraints"] == [messages[7]["content"]]
    assert "保留到我明确删" in messages[7]["content"]
    receipt = step["constraint_provenance"]["sources"][0]
    assert receipt == {"constraint_index": 0, "message_index": 7, "role": "user",
                       "content_sha256": hashlib.sha256(messages[7]["content"].encode("utf-8")).hexdigest()}


def test_quotation_negation_and_question_scope_are_not_rewritten_into_preferences():
    fixture = recorded_case()
    messages = deepcopy(fixture["payload"]["dialogue_messages"])
    # Explicit counterexample, not a claimed real model response: keep the whole
    # denial, including the quoted assistant suggestion, in its original scope.
    denial = '你说“不整段复述”，但我没有这个要求。真有必要的话可以完整重复。'
    messages.extend([{"role": "user", "content": denial}, {"role": "assistant", "content": "明白。"}])
    response = referenced_response(fixture, [3])
    projected = validate_referenced_dialogue_step(response, messages=messages, completed_turns=2, natural_utterance=True)
    assert projected["dialogue_state"]["active_constraints"] == [denial]
    assert projected["constraint_provenance"]["authority"] == "actual_user_message_source_only"
    assert "user_constraint_refs" not in projected
    response["dialogue_state"]["active_constraints"] = ["用户要求不整段复述"]
    with pytest.raises(DialogueConstraintReferenceError, match="^dialogue_constraints_are_program_owned$"):
        validate_referenced_dialogue_step(response, messages=messages, completed_turns=2, natural_utterance=True)
