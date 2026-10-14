"""Native same-task repair is explicit, source-bound, and has active bindings."""
from copy import deepcopy

import pytest

from lib.domain.human_augmentation import validate_revision_context
from lib.domain.review_repair import validate_review_repair, validate_repair_inputs
from lib.domain.workflow_creation import validate_creation
from lib.infrastructure import training_workflow as engine


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def context():
    return {"session_id": "a" * 32, "round_id": "b" * 32, "parent_run_id": "c" * 32,
            "target": "cot", "candidate_id": "original", "content_sha256": "d" * 64,
            "source_id": "e" * 64, "source_kind": "document", "teacher_evidence": "先断电再检查。",
            "instruction": "保留原题，修正错误前提。", "depth": 1,
            "ancestors": [{"run_id": "c" * 32, "candidate_id": "original"}],
            "messages": [{"role": "user", "content": "应该怎么检查？"}], "native_regeneration": True}


def options():
    ctx = context()
    return {"targets": ["cot"],
            "review_repair": {"mode": "auto", "max_rounds": 1, "native_regeneration": True},
            "repair_inputs": [{"target": "cot", "record": {"id": "original", "question": ctx["messages"]},
                               "revision_context": ctx}],
            "node_generation": {"cot": {"enabled": True, "style": "default", "answer_mode": "native_prefill"}}}


def test_missing_answer_context_never_needs_a_fake_assistant():
    value = context()
    assert validate_revision_context(value, review=True) == value
    for marker in (None, False):
        changed = deepcopy(value)
        if marker is None:
            changed.pop("native_regeneration")
        else:
            changed["native_regeneration"] = marker
        with pytest.raises(ValueError, match="invalid_human_revision_context"):
            validate_revision_context(changed, review=True)
    with pytest.raises(ValueError, match="invalid_human_revision_context"):
        validate_revision_context(value)


@pytest.mark.parametrize("field,value", [("mode", "human"), ("max_rounds", 0), ("native_regeneration", "true")])
def test_invalid_configuration_is_not_a_paid_attempt(field, value):
    config = {**options()["review_repair"], field: value}
    with pytest.raises(ValueError):
        validate_review_repair(config)


def test_config_and_context_must_both_opt_in():
    value = options()
    with pytest.raises(ValueError, match="invalid_native_regeneration_input"):
        validate_repair_inputs(value["repair_inputs"], validate_review_repair({"mode": "auto"}))
    value["repair_inputs"][0]["revision_context"].pop("native_regeneration")
    value["repair_inputs"][0]["revision_context"]["messages"].append({"role": "assistant", "content": "旧答案。"})
    with pytest.raises(ValueError, match="invalid_native_regeneration_input"):
        validate_repair_inputs(value["repair_inputs"], validate_review_repair(value["review_repair"]))


def test_normal_creation_requires_explicit_repair_and_native_style(tmp_path):
    value = options()
    for changed in ({**value, "repair_inputs": None}, {**value, "node_generation": {}},
                    {**value, "targets": ["cot", "sft"]}):
        with pytest.raises(ValueError):
            engine.create_run(tmp_path, **changed)
    assert not list(tmp_path.iterdir())
    assert validate_creation(**value) == (["cot"], {})


def test_native_child_pins_its_generation_binding_and_excludes_inactive_nodes(tmp_path):
    value = options()
    value["node_models"] = {stage: {"generation": {"backend": "offline", "model": stage + "-model"}}
                            for stage in ("cot", "review", "sft", "ingest")}
    run_id = engine.create_run(tmp_path, **value)
    recipe = engine.read_json(engine.run_path(tmp_path, run_id) / "recipe.json")
    assert set(recipe["node_models"]) == {"cot", "review"}
    assert recipe["node_models"]["cot"]["generation"]["model"] == "cot-model"
    assert recipe["native_regeneration_version"] == 1
    assert recipe["repair_inputs"][0]["revision_context"]["messages"] == context()["messages"]
    assert "assistant" not in [message["role"] for message in recipe["repair_inputs"][0]["revision_context"]["messages"]]


def test_legacy_review_configuration_is_byte_compatible():
    assert validate_review_repair({"mode": "auto", "max_rounds": 0}) == {
        "mode": "auto", "max_rounds": 0, "score_threshold": .8}
