"""Source-aware author routing, with historical checks and raw turns preserved."""
from copy import deepcopy
import json

import pytest

from lib.contexts.workflows.domain.open_dialogue_routing import (
    open_dialogue_question_route, validate_open_dialogue_question_mode,
)
from lib.infrastructure import training_workflow as engine
from lib.io_utils import atomic_json
from tests.test_dialogue_constraint_sources import recorded_case, referenced_response
from tests.test_separated_dialogue_generation import FIRST_QUESTION, make_run, task_payload


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """All models are injected; these tests never call a provider."""
    yield


def frozen_recipe(mode="auto"):
    return {"open_dialogue_routing_version": 1, "open_dialogue_question_mode": mode,
            "qa_director": {"question_separation": True}}


@pytest.mark.parametrize("options, expected", [({}, "director_direct"),
    ({"open_dialogue_question_mode": "separated"}, "source_blind_questioner"),
    ({"open_dialogue_question_mode": None}, None)])
def test_creation_defaults_to_source_aware_routing_with_explicit_legacy_and_separate_choices(tmp_path, options, expected):
    run_id = engine.create_run(tmp_path, brief="Make a local demo.", targets=["multiturn"],
        qa_director={"enabled": True, "question_separation": False}, **options)
    recipe = engine.read_json(engine.run_path(tmp_path, run_id) / "recipe.json")
    route = open_dialogue_question_route(recipe, {"kind": "brief", "synthetic": True})
    assert (route["route"] if route else None) == expected
    if expected == "source_blind_questioner":
        assert recipe["qa_director"]["question_separation"] is True


@pytest.mark.parametrize("source_changes", [
    {"kind": "document"}, {"synthetic": False}, {"human_provided": True},
    {"source_context_pack": {"materials": ["private"]}},
    {"tools": [{"name": "lookup"}]}, {"reference_answer": "private answer"},
    {"qa_contract": {"source_line_ranges": [{"start_line": 1, "end_line": 2}]}},
])
def test_attached_knowledge_and_authored_sources_keep_source_blind_questioner(source_changes):
    source = {"kind": "brief", "synthetic": True, **source_changes}
    before = deepcopy(source)
    receipt = open_dialogue_question_route(frozen_recipe(), source)
    assert receipt["route"] == "source_blind_questioner"
    assert receipt["quality_status"] == "not_assessed" and source == before


def test_explicit_separation_and_legacy_policy_are_not_silently_changed():
    scene = {"kind": "brief", "synthetic": True}
    assert open_dialogue_question_route(frozen_recipe("separated"), scene)["route"] == "source_blind_questioner"
    assert open_dialogue_question_route({"qa_director": {"question_separation": True}}, scene) is None
    manual = frozen_recipe()
    manual["qa_director"]["human_augmentation"] = {"enabled": True}
    assert open_dialogue_question_route(manual, scene)["route"] == "source_blind_questioner"
    for value in (True, "direct", [], 1):
        with pytest.raises(ValueError, match="^invalid_open_dialogue_question_mode$"):
            validate_open_dialogue_question_mode(value)
    assert validate_open_dialogue_question_mode(None) is None


def test_real_next_turn_reuses_director_utterance_and_checkpoint_without_actor_calls(tmp_path):
    fixture = recorded_case()
    payload = fixture["payload"]
    response = referenced_response(fixture, [1])

    class RecordedDirector:
        model = "offline-recorded-director"

        def __init__(self):
            self.calls = []

        def chat(self, messages, **options):
            self.calls.append(deepcopy(messages))
            if data := task_payload(messages):
                if "candidates" in data:
                    return json.dumps({"tasks": [{**payload["qa_contract"], "id": candidate["id"]}
                                                for candidate in data["candidates"]]}, ensure_ascii=False)
            return json.dumps(response, ensure_ascii=False)

    director = RecordedDirector()
    output = tmp_path / "out"
    run_id = engine.create_run(output, brief=payload["teacher_evidence"], targets=["multiturn"],
        qa_director={"enabled": True, "planning_mode": "adaptive", "question_separation": True})
    path = engine.run_path(output, run_id)
    recipe, state = engine.read_json(path / "recipe.json"), engine.read_json(path / "state.json")
    recipe.update({key: value for key, value in frozen_recipe().items() if key != "qa_director"})
    state["recipe_hash"] = engine.digest(recipe)
    atomic_json(path / "recipe.json", recipe)
    atomic_json(path / "state.json", state)
    worker = engine.Workflow(output, run_id, tmp_path, generator=director)
    worker.stage = "multiturn"
    unit = {"id": "recorded-scene", "source_id": "brief", "kind": "brief", "synthetic": True,
            "text": payload["teacher_evidence"], "qa_contract": deepcopy(payload["qa_contract"])}
    before = deepcopy(unit)
    # Run the actual initial planning stage in isolation before the follow-up.
    worker.directed_generation([unit], needs_sft=False, needs_multiturn=False)
    initial = task_payload(director.calls[0])
    assert initial["question_separation"] is False
    assert initial["candidates"][0]["question_routing"]["route"] == "director_direct"
    assert len(director.calls) == 1
    worker.stage = "multiturn"
    step = worker.next_dialogue_step(unit, payload["dialogue_messages"], payload["previous_dialogue_state"])
    again = worker.next_dialogue_step(unit, payload["dialogue_messages"], payload["previous_dialogue_state"])
    assert step == again and len(director.calls) == 2
    assert step["user_message"] == fixture["response"]["user_message"]
    assert step["question_routing"]["route"] == "director_direct"
    assert step["question_routing"]["quality_status"] == "not_assessed"
    assert step["constraint_provenance"]["semantic_status"] == "not_certified"
    sent = task_payload(director.calls[1])
    assert sent["question_separation"] is False
    assert sent["dialogue_messages"] == payload["dialogue_messages"]
    assert unit == before


def test_real_document_batch_retains_brief_actor_fit_and_source_checks(tmp_path):
    worker, author, actor, reviewer = make_run(tmp_path)
    worker.recipe.update({key: value for key, value in frozen_recipe().items() if key != "qa_director"})
    worker.state["recipe_hash"] = engine.digest(worker.recipe)
    atomic_json(worker.path / "recipe.json", worker.recipe)
    atomic_json(worker.path / "state.json", worker.state)
    state = worker.execute()
    assert state["status"] == "completed", state.get("error")
    assert len(author.brief_checks) == len(actor.calls) == len(author.fits) == 1
    assert reviewer.calls  # Selection of the author is never a quality release.
    row = engine.read_json(worker.path / "artifacts/sft.records.json")[0]
    assert next(message["content"] for message in row["messages"] if message["role"] == "user") == FIRST_QUESTION
