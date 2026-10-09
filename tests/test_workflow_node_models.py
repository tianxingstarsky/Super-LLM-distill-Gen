"""Node model choices are explicit, isolated and validated at submission."""
import pytest

from lib.application.workflow_node_models_service import WorkflowNodeModelsApplication
from lib.domain.workflow_node_models import initialize_draft, missing_bindings


class Inventory:
    def __init__(self):
        self.value = {"default_backend": "writer", "default_model": "write-v2",
                      "roles": {"jev": {"backend": "review", "model": "judge"}},
                      "backends": [{"name": "writer", "models": ["write-v1", "write-v2"]},
                                   {"name": "review", "models": ["judge"]}]}

    def list_backends(self):
        return self.value


def test_new_nodes_use_defaults_once_and_keep_explicit_choices():
    port = Inventory()
    app = WorkflowNodeModelsApplication(port)
    draft, initialized, _ = app.prepare_draft(["sft"], "文档", {}, [])
    assert draft["sft"]["generation"]["model"] == "write-v2"
    draft["sft"]["generation"]["model"] = "custom-model"
    port.value["default_model"] = "write-v1"
    expanded, _, _ = app.prepare_draft(["sft", "preference"], "文档", draft, initialized)
    assert expanded["sft"]["generation"]["model"] == "custom-model"
    assert expanded["preference"]["generation"]["model"] == "write-v1"
    assert draft.get("preference") is None


def test_absent_services_do_not_mark_empty_drafts_initialized():
    inventory = Inventory().value
    unavailable = {**inventory, "backends": []}
    draft, initialized, _ = initialize_draft(["sft"], "文档", {}, [], unavailable)
    assert draft == {} and initialized == []
    recovered, _, endpoints = initialize_draft(["sft"], "文档", draft, initialized, inventory)
    assert not missing_bindings(["sft"], "文档", recovered, endpoints)


def test_discovered_model_capacity_initializes_new_role_without_rewriting_saved_draft():
    port = Inventory()
    writer = port.value["backends"][0]
    writer["discovered_models"] = ["discovered-writer"]
    writer["model_info"] = {"discovered-writer": {
        "context_window_tokens": 200_000, "max_output_tokens": 8192}}
    port.value["default_model"] = "discovered-writer"
    app = WorkflowNodeModelsApplication(port)
    draft, initialized, _ = app.prepare_draft(["sft"], "文档", {}, [])
    assert draft["sft"]["generation"] == {
        "backend": "writer", "model": "discovered-writer",
        "context_window_tokens": 200_000, "max_output_tokens": 8192}
    writer["model_info"]["discovered-writer"]["max_output_tokens"] = 65_536
    draft["sft"]["generation"]["context_window_tokens"] = 150_000
    saved, _, _ = app.prepare_draft(["sft", "preference"], "文档", draft, initialized)
    assert saved["sft"]["generation"]["context_window_tokens"] == 150_000
    assert saved["sft"]["generation"]["max_output_tokens"] == 8192
    assert saved["preference"]["generation"]["max_output_tokens"] == 32_768
    assert writer["models"] == ["write-v1", "write-v2"]


def test_invalid_discovery_limits_do_not_break_initial_draft():
    port = Inventory()
    port.value["backends"][0]["model_info"] = {"write-v2": {
        "context_window_tokens": True, "max_output_tokens": -1}}
    draft, _, _ = WorkflowNodeModelsApplication(port).prepare_draft(["sft"], "文档", {}, [])
    assert draft["sft"]["generation"]["context_window_tokens"] == 131_072
    assert draft["sft"]["generation"]["max_output_tokens"] == 32_768


def test_unlisted_role_default_requires_an_explicit_node_model_choice():
    port = Inventory()
    port.value["roles"]["jev"] = {"backend": "review", "model": "custom-judge"}
    app = WorkflowNodeModelsApplication(port)

    draft, initialized, endpoints = app.prepare_draft(["sft"], "文档", {}, [])
    assert draft["sft"]["generation"]["model"] == "write-v2"
    assert "jev" not in draft["sft"]
    assert "sft:jev" not in initialized
    assert missing_bindings(["sft"], "文档", draft, endpoints) == [("sft", "jev")]
    with pytest.raises(ValueError, match="选择服务"):
        app.snapshot(["sft"], "文档", draft)

    # A model deliberately entered on the node remains supported, including
    # names not yet advertised by the endpoint's model list.
    draft["sft"]["jev"] = {"backend": "review", "model": "custom-judge"}
    assert app.snapshot(["sft"], "文档", draft)["sft"]["jev"]["model"] == "custom-judge"


def test_unlisted_global_default_does_not_fall_back_silently():
    port = Inventory()
    port.value["roles"] = {}
    port.value["default_model"] = "unlisted-writer"

    draft, initialized, endpoints = WorkflowNodeModelsApplication(port).prepare_draft(
        ["sft"], "文档", {}, [])
    assert draft == {}
    assert initialized == []
    assert missing_bindings(["sft"], "文档", draft, endpoints) == [
        ("sft", "generation"), ("sft", "jev")]


def test_removed_service_never_silently_rebinds_and_submission_rechecks_inventory():
    port = Inventory()
    app = WorkflowNodeModelsApplication(port)
    draft, initialized, _ = app.prepare_draft(["sft"], "文档", {}, [])
    port.value["backends"] = [port.value["backends"][0]]
    preserved, _, endpoints = app.prepare_draft(["sft"], "文档", draft, initialized)
    assert preserved["sft"]["jev"]["backend"] == "review"
    assert missing_bindings(["sft"], "文档", preserved, endpoints) == [("sft", "jev")]
    with pytest.raises(ValueError, match="选择服务"):
        app.snapshot(["sft"], "文档", draft)


def test_snapshot_keeps_custom_models_and_only_current_node_roles():
    port = Inventory()
    app = WorkflowNodeModelsApplication(port)
    draft, _, _ = app.prepare_draft(["sft", "cpt"], "开放需求", {}, [])
    draft["sft"]["generation"]["model"] = "custom-model"
    result = app.snapshot(["sft", "cpt"], "文档", draft)
    assert set(result) == {"sft"}
    assert result["sft"]["generation"]["model"] == "custom-model"
    result["sft"]["generation"]["model"] = "changed"
    assert draft["sft"]["generation"]["model"] == "custom-model"


def test_same_model_can_fill_all_nodes_and_roles_with_independent_token_limits():
    app = WorkflowNodeModelsApplication(Inventory())
    binding = {"backend": "writer", "model": "write-v2", "context_window_tokens": 196_608,
               "max_output_tokens": 65_536}
    draft = {node: {"generation": dict(binding), "jev": {**binding, "max_output_tokens": 32_768}}
             for node in ("sft", "multiturn", "cot")}
    result = app.snapshot(list(draft), "文档", draft, node_generation={"cot": {"enabled": True}})
    assert result == draft
    result["cot"]["generation"]["max_output_tokens"] = 48_000
    assert result["cot"]["jev"]["max_output_tokens"] == 32_768
    assert result["sft"]["generation"]["max_output_tokens"] == 65_536
    assert draft["cot"]["generation"]["max_output_tokens"] == 65_536


@pytest.mark.parametrize('configuration', [{}, {'cot': {'enabled': False, 'style': 'custom'}}])
def test_ordinary_cot_needs_only_reviewer_and_preserves_optional_writer_choice(configuration):
    app = WorkflowNodeModelsApplication(Inventory())
    draft, initialized, endpoints = app.prepare_draft(
        ['cot'], '文档', {}, [], node_generation=configuration)
    assert set(draft['cot']) == {'jev'}
    assert initialized == ['cot:jev']
    assert missing_bindings(['cot'], '文档', draft, endpoints, node_generation=configuration) == []
    assert set(app.snapshot(['cot'], '文档', draft, node_generation=configuration)['cot']) == {'jev'}

    styled = {'cot': {'enabled': True, 'style': 'structured'}}
    expanded, initialized, _ = app.prepare_draft(
        ['cot'], '文档', draft, initialized, node_generation=styled)
    assert set(expanded['cot']) == {'generation', 'jev'}
    ordinary, _, _ = app.prepare_draft(
        ['cot'], '文档', expanded, initialized, node_generation=configuration)
    assert ordinary['cot']['generation'] == expanded['cot']['generation']
    assert set(app.snapshot(['cot'], '文档', ordinary, node_generation=configuration)['cot']) == {'jev'}


def test_enabled_cot_style_requires_writer_before_submission():
    app = WorkflowNodeModelsApplication(Inventory())
    configuration = {'cot': {'style': 'structured'}}
    bindings = {'cot': {'jev': {'backend': 'review', 'model': 'judge'}}}
    with pytest.raises(ValueError, match='选择服务'):
        app.snapshot(['cot'], '文档', bindings, node_generation=configuration)


def test_omitting_style_configuration_keeps_existing_model_draft_contract():
    from lib.domain.workflow_scale import node_roles
    assert node_roles('cot', '文档') == ('jev',)
    assert node_roles('cot', '文档', node_generation={}) == ('jev',)
    assert node_roles('cot', '文档', node_generation={'cot': {'style': 'concise'}}) == ('generation', 'jev')


def test_incomplete_custom_instruction_does_not_crash_model_draft_preparation():
    app = WorkflowNodeModelsApplication(Inventory())
    incomplete = {'cot': {'enabled': True, 'style': 'custom', 'instruction': ''}}
    draft, _, _ = app.prepare_draft(['cot'], '文档', {}, [], node_generation=incomplete)
    assert set(draft['cot']) == {'generation', 'jev'}
    assert set(app.snapshot(['cot'], '文档', draft, node_generation=incomplete)['cot']) == {'generation', 'jev'}
    from lib.domain.workflow_creation import validate_creation
    with pytest.raises(ValueError, match='invalid_node_generation'):
        validate_creation(targets=['cot'], node_generation=incomplete)
