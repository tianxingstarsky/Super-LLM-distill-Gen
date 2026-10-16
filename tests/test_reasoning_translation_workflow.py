"""New runs freeze translation rules without changing historical requests."""
from copy import deepcopy

import pytest

from lib.domain.workflow_generation import reasoning_language_instruction, style_for_sample
from lib.infrastructure import training_workflow as engine


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


STYLE = {"enabled": True, "style": "concise", "reasoning_language": "zh_en",
         "instruction": "Preserve every task-relevant condition and inference."}


@pytest.mark.parametrize("stage", ["sft", "cot", "multiturn"])
def test_new_run_pins_complete_translation_for_each_authored_node(tmp_path, stage):
    output = tmp_path / "output"
    run_id = engine.create_run(output, brief="Explain the conditions for the decision.",
        targets=[stage], node_generation={stage: STYLE})
    worker = engine.Workflow(output, run_id, tmp_path)
    assert worker.recipe["reasoning_language_contract_version"] == 3
    assert worker.recipe["knowledge_application_voice_version"] == 3
    style = worker.generation_style(stage, "sample")
    expected_rule = reasoning_language_instruction("zh_en", version=3)
    assert style["instruction"].count(expected_rule) == 1
    assert style["instruction"] == worker.recipe["node_generation_prompts"][stage]["concise"]
    assert "不做逐句双语翻译" not in style["instruction"]
    assert worker.recipe["node_generation"][stage]["instruction"] == STYLE["instruction"]


@pytest.mark.parametrize("pinned", [False, True])
def test_legacy_recipe_reconstructs_exact_original_rule_without_migration(tmp_path, pinned):
    output = tmp_path / "output"
    run_id = engine.create_run(output, brief="Preserve this old task.", targets=["sft"],
                               node_generation={"sft": STYLE})
    worker = engine.Workflow(output, run_id, tmp_path)
    worker.recipe.pop("reasoning_language_contract_version")
    worker.recipe.pop("authored_reasoning_language_review_version")
    legacy_style = style_for_sample(STYLE, "sample")
    worker.recipe["node_generation_prompts"] = ({"sft": {"concise": legacy_style["instruction"]}}
                                                if pinned else {})
    before = deepcopy(worker.recipe)
    assert worker.generation_style("sft", "sample") == legacy_style
    assert worker.recipe == before


def test_pinned_custom_rule_gains_only_its_own_frozen_language_contract(tmp_path):
    output = tmp_path / "output"
    run_id = engine.create_run(output, brief="Explain a decision.", targets=["sft"],
                               node_generation={"sft": STYLE})
    worker = engine.Workflow(output, run_id, tmp_path)
    worker.recipe["node_generation_prompts"]["sft"]["concise"] = "Keep the saved editorial rule verbatim."
    before = deepcopy(worker.recipe)
    actual = worker.generation_style("sft", "sample")
    assert actual["instruction"] == ("Keep the saved editorial rule verbatim.\n"
                                       + reasoning_language_instruction("zh_en", version=3))
    assert worker.recipe == before
