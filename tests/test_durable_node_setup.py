"""Model references and prompt edits form one restartable local draft."""
from copy import deepcopy
import json

import pytest
from streamlit.testing.v1 import AppTest

from lib.bootstrap.creation_drafts import creation_draft_application
from lib.domain.creation_draft import validate_creation_draft
from lib.presentation.streamlit.workflow_reuse import recipe_to_draft


BINDING = {"backend": "writer", "model": "custom-writer",
           "context_window_tokens": 196608, "max_output_tokens": 49152}


def screen(output):
    from pathlib import Path
    import streamlit as st
    from lib.bootstrap.creation_drafts import creation_draft_application
    from lib.application.workflow_node_models_service import WorkflowNodeModelsApplication
    from lib.presentation.streamlit.workflow_node_settings import (
        node_bindings, render_node_models, _persist_draft_value,
    )
    from lib.presentation.streamlit.work_drafts import render_work_drafts

    class Inventory:
        def list_backends(self):
            return {"backends": [] if st.session_state.get("fixture-removed") else [
                {"name": "writer", "models": ["alpha", "custom-writer"]},
                {"name": "judge", "models": ["beta"]}], "roles": {},
                "default_backend": "writer" if st.session_state.get("fixture-defaults") else None,
                "default_model": "alpha" if st.session_state.get("fixture-defaults") else None}

    drafts = creation_draft_application(Path(output))
    st.session_state["workflow-draft-application:demo"] = drafts
    if "workflow-form-draft:demo" not in st.session_state:
        st.session_state["workflow-form-draft:demo"] = drafts.load()
    node = st.radio("Node", ("sft", "preference", "package"), key="fixture-node")
    bindings, endpoints = node_bindings(WorkflowNodeModelsApplication(Inventory()),
                                        ["sft", "preference", "cot", "package"],
                                        "文档资料", "demo", node_generation={},
                                        package_review={"enabled": True})
    render_node_models(node, "文档资料", "demo", bindings, endpoints,
                       node_generation={}, package_review={"enabled": True})
    key = "workflow-node-prompt:demo:sft:workflow.sft"
    if key not in st.session_state:
        st.session_state[key] = st.session_state["workflow-form-draft:demo"].get(key, "Original prompt")
    st.text_area("Prompt", key=key, on_change=lambda: _persist_draft_value("demo", key, st.session_state[key]))
    st.button("Save separate draft", key="fixture-snapshot",
              on_click=lambda: drafts.save_snapshot("Complete draft", values=st.session_state["workflow-form-draft:demo"]))
    render_work_drafts(drafts, "demo", lambda page: None)


def test_model_and_prompt_restore_together_after_restart_and_named_restore(tmp_path):
    app = creation_draft_application(tmp_path)
    original = {"workflow-node-bindings:demo": {"sft": {"generation": deepcopy(BINDING)}},
                "workflow-node-prompt:demo:sft:workflow.sft": "Retain literal {source}."}
    app.replace(original)
    ui = AppTest.from_function(screen, args=(str(tmp_path),)).run()
    assert not ui.exception
    assert ui.selectbox(key="node-model:demo:sft:generation:model:writer").value == "custom-writer"
    ui.number_input(key="node-model:demo:sft:generation:output:writer:custom-writer").set_value(65536).run()
    ui.text_area(key="workflow-node-prompt:demo:sft:workflow.sft").set_value("New literal {source} instruction.").run()
    ui.button(key="fixture-snapshot").click().run()
    saved_id = app.snapshots()[0]["id"]
    expected = app.load()
    ui.radio(key="fixture-node").set_value("preference").run()
    ui.radio(key="fixture-node").set_value("sft").run()
    assert ui.number_input(key="node-model:demo:sft:generation:output:writer:custom-writer").value == 65536
    fresh = AppTest.from_function(screen, args=(str(tmp_path),)).run()
    assert not fresh.exception
    assert fresh.session_state["workflow-form-draft:demo"] == expected
    assert fresh.text_area(key="workflow-node-prompt:demo:sft:workflow.sft").value == "New literal {source} instruction."
    fresh.number_input(key="node-model:demo:sft:generation:output:writer:custom-writer").set_value(40000).run()
    fresh.selectbox(key="work-draft-selected:demo:0").set_value(saved_id).run()
    fresh.button(key="work-draft-open:demo").click().run()
    assert not fresh.exception
    assert fresh.number_input(key="node-model:demo:sft:generation:output:writer:custom-writer").value == 65536
    assert fresh.session_state["workflow-form-draft:demo"] == expected


def test_batch_fill_copies_only_missing_matching_roles_and_preserves_overrides(tmp_path):
    judge = {**BINDING, "backend": "judge", "model": "beta"}
    original = {"sft": {"generation": deepcopy(BINDING), "jev": judge},
                "preference": {"generation": {**BINDING, "model": "alpha"}},
                "package": {"jev": {**judge, "max_output_tokens": 40000}}}
    app = creation_draft_application(tmp_path)
    app.replace({"workflow-node-bindings:demo": original})
    ui = AppTest.from_function(screen, args=(str(tmp_path),)).run()
    ui.button(key="node-model-fill:demo:sft").click().run()
    assert not ui.exception
    bindings = app.load()["workflow-node-bindings:demo"]
    assert bindings["preference"]["generation"] == original["preference"]["generation"]
    assert bindings["preference"]["jev"] == judge
    assert bindings["package"] == original["package"]
    # Ordinary CoT requires only a reviewer; never add a generator implicitly.
    assert bindings["cot"] == {"jev": judge}
    ui.radio(key="fixture-node").set_value("preference").run()
    ui.number_input(key="node-model:demo:preference:jev:output:judge:beta").set_value(42000).run()
    assert app.load()["workflow-node-bindings:demo"]["sft"]["jev"] == judge


def test_removed_connection_is_visible_preserved_and_never_substituted(tmp_path):
    app = creation_draft_application(tmp_path)
    original = {"sft": {"generation": deepcopy(BINDING)}}
    app.replace({"workflow-node-bindings:demo": original})
    ui = AppTest.from_function(screen, args=(str(tmp_path),))
    ui.session_state["fixture-removed"] = True
    ui.run()
    assert not ui.exception
    assert any("原选择保留在草稿中" in item.value for item in ui.warning)
    assert app.load()["workflow-node-bindings:demo"] == original
    ui.session_state["fixture-removed"] = False
    ui.run()
    assert ui.selectbox(key="node-model:demo:sft:generation:model:writer").value == "custom-writer"


def test_explicit_empty_node_is_not_repopulated_from_defaults_after_restart(tmp_path):
    app = creation_draft_application(tmp_path)
    app.replace({"workflow-node-bindings:demo": {"sft": {}}})
    fresh = AppTest.from_function(screen, args=(str(tmp_path),))
    fresh.session_state["fixture-defaults"] = True
    fresh.run()
    assert not fresh.exception
    assert fresh.selectbox(key="node-model:demo:sft:generation:backend").value is None


def test_latest_token_edit_survives_a_simultaneous_node_switch(tmp_path):
    app = creation_draft_application(tmp_path)
    app.replace({"workflow-node-bindings:demo": {"sft": {"generation": deepcopy(BINDING)}}})
    ui = AppTest.from_function(screen, args=(str(tmp_path),)).run()
    ui.number_input(key="node-model:demo:sft:generation:output:writer:custom-writer").set_value(65536)
    ui.radio(key="fixture-node").set_value("preference").run()
    assert not ui.exception
    assert app.load()["workflow-node-bindings:demo"]["sft"]["generation"]["max_output_tokens"] == 65536
    ui.radio(key="fixture-node").set_value("sft").run()
    assert ui.number_input(key="node-model:demo:sft:generation:output:writer:custom-writer").value == 65536


def test_recipe_copy_persists_model_references_and_prompts_without_mutating_recipe(tmp_path):
    recipe = {"sources": [], "targets": ["sft"], "brief": "Build examples", "sample_count": 1000,
              "node_models": {"sft": {"generation": deepcopy(BINDING)}},
              "node_prompts": {"sft": {"workflow.sft": "Use {source}."}},
              "endpoint_pins": {"sft": {"api_key": "NEVER_COPY_SECRET"}}}
    before = deepcopy(recipe)
    copied = recipe_to_draft(recipe, "Task", "demo", [])
    values = copied["values"]
    app = creation_draft_application(tmp_path)
    app.replace(values)
    snapshot = app.save_snapshot()
    assert creation_draft_application(tmp_path).load()["workflow-node-bindings:demo"] == recipe["node_models"]
    assert app.restore_snapshot(snapshot)["workflow-node-prompt:demo:sft:workflow.sft"] == "Use {source}."
    assert "NEVER_COPY_SECRET" not in json.dumps(app.load())
    copied["node_bindings"]["sft"]["generation"]["model"] = "other"
    assert values["workflow-node-bindings:demo"] == recipe["node_models"]
    assert recipe == before


def test_production_recipe_copy_keeps_delivery_goals_and_budget():
    production = {"goals": {"sft": 1000000, "dpo": 500000}, "budget_usd": 1250.5}
    recipe = {"sources": [], "targets": ["sft", "dpo"], "sample_count": 10000,
              "production": production}
    before = deepcopy(recipe)
    values = recipe_to_draft(recipe, "Million task", "demo", [])["values"]
    assert values["workflow-production-enabled:demo"] is True
    assert values["workflow-production-goals:demo"] == production["goals"]
    assert values["workflow-production-budget:demo"] == 1250.5
    assert values["workflow-count:demo"] == 1000000
    assert recipe == before


def test_legacy_recipe_copy_does_not_enable_automatic_refill():
    values = recipe_to_draft({"sources": [], "targets": ["sft"], "sample_count": 50000},
                             "Old task", "demo", [])["values"]
    assert values["workflow-production-enabled:demo"] is False
    assert values["workflow-count:demo"] == 50000
    assert "workflow-production-goals:demo" not in values


def test_copied_package_review_preserves_explicit_failure_escalation():
    copied = recipe_to_draft({"sources": [], "targets": ["sft"], "sample_count": 1000,
                              "package_review": {"enabled": True, "escalate_failure_percent": 7.5}},
                             "Review task", "demo", [])
    assert copied["values"]["workflow-package-review-escalation:demo"] == 7.5


def test_copy_infers_legacy_agent_mode_without_copying_container_or_credentials():
    copied = recipe_to_draft({"sources": [], "targets": ["agent"], "sample_count": 1000,
                              "agent_sandbox_image": "NEVER_COPY_PRIVATE_CONTAINER_PIN"},
                             "Agent task", "demo", [])
    assert copied["values"]["workflow-agent-mode:demo"] == "isolated"
    assert "NEVER_COPY" not in json.dumps(copied)


@pytest.mark.parametrize("key,value", [
    ("workflow-node-bindings:demo:other", {}), ("workflow-node-bindings:../escape", {}),
    ("workflow-node-bindings:demo", {"unknown": {}}),
    ("workflow-node-bindings:demo", {"sft": {"vision": BINDING}}),
    ("workflow-node-bindings:demo", {"sft": {"generation": {**BINDING, "api_key": "secret"}}}),
    ("workflow-node-bindings:demo", {"sft": {"generation": {**BINDING, "base_url": "https://secret.test"}}}),
    ("workflow-node-bindings:demo", {"sft": {"generation": {**BINDING, "max_output_tokens": True}}}),
    ("workflow-node-bindings:demo", {"sft": {"generation": {**BINDING, "model": "x\x00y"}}}),
    ("workflow-node-bindings:demo", {"sft": {"generation": {**BINDING, "max_output_tokens": 196608}}}),
    ("workflow-document-parse-mode:demo:other", "vision"),
    ("workflow-agent-mode:demo", "configured"),
])
def test_durable_models_reject_secrets_invalid_limits_and_invalid_scopes_before_write(tmp_path, key, value):
    with pytest.raises(ValueError, match="invalid_creation_draft"):
        creation_draft_application(tmp_path).replace({key: value})
    assert not (tmp_path / ".creation-draft.json").exists()


def test_draft_cannot_mix_other_users_workspace_keys():
    with pytest.raises(ValueError, match="invalid_creation_draft"):
        validate_creation_draft({"workflow-node-bindings:demo": {}, "workflow-name:other": "Other task"})


def test_model_validation_and_snapshot_are_independent_copies(tmp_path):
    values = {"workflow-node-bindings:demo": {"sft": {"generation": deepcopy(BINDING)}}}
    copy = validate_creation_draft(values)
    copy["workflow-node-bindings:demo"]["sft"]["generation"]["model"] = "changed"
    assert values["workflow-node-bindings:demo"]["sft"]["generation"] == BINDING
    app = creation_draft_application(tmp_path)
    app.replace(values)
    identifier = app.save_snapshot()
    values["workflow-node-bindings:demo"]["sft"]["generation"]["model"] = "changed"
    assert app.restore_snapshot(identifier)["workflow-node-bindings:demo"]["sft"]["generation"] == BINDING
