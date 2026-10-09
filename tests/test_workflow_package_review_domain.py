"""Package review is opt-in, node-scoped and safe for immutable older runs."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from lib.application.workflow_node_models_service import WorkflowNodeModelsApplication
from lib.application.workflow_service import WorkflowApplication
from lib.domain.creation_draft import validate_creation_draft
from lib.domain.workflow_node_prompts import (
    LEGACY_NODE_PROMPT_IDS, active_node_prompt_ids, builtin_node_prompt,
    snapshot_node_prompts, validate_node_prompt_snapshot,
)
from lib.domain.workflow_package_review import validate_package_review
from lib.domain.workflow_scale import node_roles, validate_node_models


def test_package_review_is_opt_in_and_returns_independent_normalized_config():
    assert validate_package_review(None) == {"enabled": False}
    assert validate_package_review({}) == {"enabled": False}
    assert validate_package_review({"enabled": False, "mode": "all"}) == {"enabled": False}
    assert validate_package_review({"enabled": True}) == {
        "enabled": True, "mode": "sample", "sample_percent": 1.0,
        "max_samples_per_target": 1000}
    value = {"enabled": True, "mode": "all", "sample_percent": 100,
             "max_samples_per_target": 10_000}
    result = validate_package_review(value)
    result["sample_percent"] = 2
    assert value["sample_percent"] == 100
    assert validate_package_review({"enabled": True, "sample_percent": 0.01})["sample_percent"] == 0.01


@pytest.mark.parametrize("value", [
    False, [], {"enabled": 1}, {"enabled": True, "mode": "random"},
    {"enabled": True, "sample_percent": True},
    {"enabled": True, "sample_percent": 0},
    {"enabled": True, "sample_percent": 0.001},
    {"enabled": True, "sample_percent": 101},
    {"enabled": True, "sample_percent": float("nan")},
    {"enabled": True, "sample_percent": float("inf")},
    {"enabled": True, "sample_percent": 10 ** 1000},
    {"enabled": True, "max_samples_per_target": True},
    {"enabled": True, "max_samples_per_target": 0},
    {"enabled": True, "max_samples_per_target": 10001},
    {"enabled": True, "unknown": "setting"},
])
def test_invalid_review_settings_fail_before_storage(value):
    driver = Mock()
    with pytest.raises(ValueError, match="invalid_package_review"):
        WorkflowApplication(driver).create_run(package_review=value)
    driver.create.assert_not_called()


def test_application_forwards_normalized_review_without_mutating_input():
    driver = Mock()
    config = {"enabled": True, "sample_percent": 0.5}
    WorkflowApplication(driver).create_run(targets=["cpt"], package_review=config)
    forwarded = driver.create.call_args.kwargs["package_review"]
    assert forwarded == {"enabled": True, "mode": "sample", "sample_percent": 0.5,
                         "max_samples_per_target": 1000}
    assert config == {"enabled": True, "sample_percent": 0.5}


def test_model_and_prompt_controls_only_activate_when_review_is_enabled():
    assert node_roles("package", "文档") == ()
    assert active_node_prompt_ids("package", "开放需求") == ()
    enabled = {"enabled": True}
    assert node_roles("package", "文档", package_review=enabled) == ("jev",)
    assert active_node_prompt_ids("package", "文档", package_review=enabled) == (
        "workflow.package_review",)
    assert "拒选回答是有意提供的负例" in builtin_node_prompt("workflow.package_review")
    with pytest.raises(ValueError, match="invalid_node_models"):
        validate_node_models({"package": {"generation": {"backend": "judge", "model": "m"}}})


def test_package_model_draft_is_preserved_when_switch_is_off_but_not_submitted():
    inventory = Mock()
    inventory.list_backends.return_value = {
        "default_backend": "judge", "default_model": "review-model",
        "backends": [{"name": "judge", "models": ["review-model"]}]}
    app = WorkflowNodeModelsApplication(inventory)
    draft, initialized, _ = app.prepare_draft(["package"], "文档", {}, [])
    assert draft == {} and initialized == []
    enabled = {"enabled": True}
    draft, initialized, _ = app.prepare_draft(
        ["package"], "文档", draft, initialized, package_review=enabled)
    assert draft["package"]["jev"]["model"] == "review-model"
    assert initialized == ["package:jev"]
    assert app.snapshot(["package"], "文档", draft) == {}
    assert app.snapshot(["package"], "文档", draft, package_review=enabled) == draft
    with pytest.raises(ValueError, match="选择服务"):
        app.snapshot(["package"], "文档", {}, package_review=enabled)


def test_snapshot_accepts_exact_legacy_catalog_without_adding_new_templates():
    system = "FROZEN_SYSTEM"
    legacy = {stage: {prompt_id: "FROZEN " + prompt_id for prompt_id in ids}
              for stage, ids in LEGACY_NODE_PROMPT_IDS.items()}
    original = deepcopy(legacy)
    assert validate_node_prompt_snapshot(legacy, system, recipe_version=9) == original
    assert "package" not in legacy
    with pytest.raises(ValueError, match="invalid_node_prompt_snapshot"):
        validate_node_prompt_snapshot(legacy, system, recipe_version=10)
    current = snapshot_node_prompts({"package": {"workflow.package_review": "CUSTOM {literal}"}})
    assert validate_node_prompt_snapshot(current, system, recipe_version=10) == current
    assert current["package"]["workflow.package_review"] == "CUSTOM {literal}"
    with pytest.raises(ValueError, match="invalid_node_prompt_snapshot"):
        validate_node_prompt_snapshot(current, system, recipe_version=9)


def test_package_review_creation_draft_is_bounded_and_literal():
    draft = {"workflow-package-review-enabled:default": True,
             "workflow-package-review-mode:default": "sample",
             "workflow-package-review-percent:default": 0.5,
             "workflow-package-review-limit:default": 1000,
             "workflow-node-prompt:default:package:workflow.package_review": "Return {literal}"}
    assert validate_creation_draft(draft) == draft
    for key, value in (("workflow-package-review-percent:default", float("nan")),
                       ("workflow-package-review-percent:default", True),
                       ("workflow-package-review-percent:default", 0),
                       ("workflow-package-review-mode:default", "unchecked"),
                       ("workflow-package-review-limit:default", 10001)):
        with pytest.raises(ValueError, match="invalid_creation_draft"):
            validate_creation_draft({key: value})


def test_package_node_pins_endpoint_prices_and_uses_shared_budget(tmp_path, monkeypatch):
    from lib import llm_client
    from lib.infrastructure.training_workflow import Workflow, create_run, read_json, run_path

    config = tmp_path / "settings" / "configs"
    config.mkdir(parents=True)
    (config / "preferences.yaml").write_text(
        (Path(__file__).resolve().parents[1] / "configs" / "preferences.yaml").read_text(encoding="utf-8"),
        encoding="utf-8")
    entry = {"base_url": "https://review.example/v1", "api_format": "responses",
             "models": ["review-model"], "prices": {"input_per_1m_usd": 1,
                                                    "output_per_1m_usd": 2}}
    (config / "backends.yaml").write_text(yaml.safe_dump({
        "backends": {"review": entry}, "budget": {"max_total_usd": 20, "hard_stop": True}}),
        encoding="utf-8")
    output = tmp_path / "output"
    bindings = {"package": {"jev": {"backend": "review", "model": "review-model",
                                     "context_window_tokens": 131072, "max_output_tokens": 32768}}}
    rid = create_run(output, brief="Make arithmetic practice", targets=["gsm8k"],
                     settings_root=config.parent, node_models=bindings,
                     package_review={"enabled": True})
    recipe = read_json(run_path(output, rid) / "recipe.json")
    assert recipe["endpoint_pins"]["package"]["jev"]["prices"] == entry["prices"]
    constructed = []

    def client(**kwargs):
        constructed.append(kwargs)
        return SimpleNamespace(client=SimpleNamespace(base_url=kwargs["base_url"]),
                               model=kwargs["model"], api_format=kwargs["api_format"], usage={})

    monkeypatch.setattr(llm_client, "ChatClient", client)
    workflow = Workflow(output, rid, config.parent)
    workflow.stage = "package"
    workflow.client("jev")
    assert constructed[0]["model"] == "review-model"
    assert constructed[0]["api_format"] == "responses"
    assert constructed[0]["context_window_tokens"] == 131072
    assert constructed[0]["budget"].limit == 20
    assert "package.jev" in workflow.state["models"]
