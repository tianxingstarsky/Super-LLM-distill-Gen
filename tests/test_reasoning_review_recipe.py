"""New runs freeze full-turn review and reasoning-preserving correction."""
from unittest.mock import Mock

import pytest

from lib.application.workflow_service import WorkflowApplication
from lib.infrastructure import training_workflow as engine


def test_new_recipe_freezes_reasoning_review_contracts(tmp_path):
    run_id = engine.create_run(tmp_path, brief="解释设备检查的先决条件。", targets=["sft"],
                               reasoning_required=True, review_repair={"mode": "auto"})
    recipe = engine.read_json(engine.run_path(tmp_path, run_id) / "recipe.json")
    assert recipe["review_context_protocol_version"] == 2
    assert recipe["turn_review_protocol_version"] == 1
    assert recipe["claim_review_protocol_version"] == 1
    assert recipe["reasoning_revision_protocol_version"] == 1
    assert recipe["multiturn_terminal_review_version"] == 1
    assert recipe["training_surface_protocol_version"] == 2
    assert recipe["reasoning_required"] is True
    assert recipe["prompts"]["workflow.review_score"]["version"] == "1.12.0"
    assert recipe["prompts"]["workflow.review_repair"]["version"] == "1.11.0"
    assert recipe["authored_reasoning_warmup_version"] == 1
    assert recipe["reasoning_distillation_audit_version"] == 1


@pytest.mark.parametrize("value", [1, "yes", None, []])
def test_invalid_reasoning_requirement_never_reaches_storage(value):
    driver = Mock()
    with pytest.raises(ValueError, match="invalid_reasoning_required"):
        WorkflowApplication(driver).create_run(targets=["sft"], reasoning_required=value)
    driver.create.assert_not_called()


def test_application_forwards_reasoning_requirement_without_rewriting_it():
    driver = Mock()
    WorkflowApplication(driver).create_run(targets=["sft"], reasoning_required=True)
    assert driver.create.call_args.kwargs["reasoning_required"] is True
