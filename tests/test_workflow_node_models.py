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
