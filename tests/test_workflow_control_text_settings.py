"""Explicit edits cannot corrupt the saved rules or their recovery copy."""
from copy import deepcopy
import pytest
from streamlit.testing.v1 import AppTest

@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield

SCRIPT = '''
import streamlit as st
from lib.domain.creation_draft import validate_creation_draft
from lib.presentation.streamlit.workflow_page import _commit_draft_fields
from lib.presentation.streamlit.workflow_control_text_settings import render_control_text_settings, control_text_snapshot
class DraftStore:
    def replace(self, values):
        if st.session_state.get("fail-save"):
            raise OSError("fixture unavailable")
        st.session_state["disk"] = validate_creation_draft(values)
st.session_state["workflow-draft-application:fixture"] = DraftStore()
node = st.radio("Node", ("sft", "multiturn", "cot"), key="fixture-node")
render_control_text_settings(node, "fixture", save_fields=_commit_draft_fields)
st.session_state["snapshot"] = control_text_snapshot("fixture", ("sft", "multiturn", "cot"))
'''

def _key(field, *, node="sft", rule=0):
    return f"control-text-ui:fixture:{node}:{rule}:{field}"

def _button(ui, name, node="sft"):
    return ui.button(key=f"control-text-ui:fixture:{node}:{name}")

def _saved(ui, node="sft"):
    return ui.session_state["workflow-form-draft:fixture"][f"workflow-control-text:fixture:{node}"]

def _initial_save(ui):
    _button(ui, "edit").click().run()
    ui.toggle(key=_key("enabled", rule="all")).set_value(True).run()
    _button(ui, "save").click().run()

def test_read_only_edit_preview_cancel_and_explicit_save():
    ui = AppTest.from_string(SCRIPT).run()
    assert not ui.exception
    assert ui.session_state["snapshot"] == {}
    assert ui.toggle(key=_key("enabled", rule="all")).disabled
    assert ui.text_input(key=_key("marker")).disabled
    _button(ui, "edit").click().run()
    ui.toggle(key=_key("enabled", rule="all")).set_value(True).run()
    ui.text_input(key=_key("marker")).set_value("[[MY_VERBOSITY]]").run()
    assert ui.session_state["snapshot"] == {}
    assert ui.code[0].value == "Desired oververbosity 5. I will check the evidence before answering."
    _button(ui, "cancel").click().run()
    assert ui.session_state["snapshot"] == {}
    assert ui.text_input(key=_key("marker")).value == "[[SJ_OUTPUT_VERBOSITY:5]]"
    _initial_save(ui)
    assert _saved(ui)["schema"].endswith("/v2")
    assert _saved(ui)["enabled"]
    assert ui.session_state["disk"] == ui.session_state["workflow-form-draft:fixture"]
    assert ui.text_input(key=_key("marker")).disabled

def test_invalid_edit_never_overwrites_saved_rule_and_failure_keeps_retry():
    ui = AppTest.from_string(SCRIPT).run()
    _initial_save(ui)
    saved = deepcopy(_saved(ui))
    disk = deepcopy(ui.session_state["disk"])
    _button(ui, "edit").click().run()
    ui.radio(key=_key("mode")).set_value("replace").run()
    assert ui.error and _button(ui, "save").disabled
    assert _saved(ui) == saved
    assert ui.session_state["snapshot"]["sft"] == saved
    ui.text_area(key=_key("replacement")).set_value("按需说明细节").run()
    ui.session_state["fail-save"] = True
    _button(ui, "save").click().run()
    assert _saved(ui) == saved and ui.session_state["disk"] == disk
    assert any("保存失败" in error.value for error in ui.error)
    assert ui.text_area(key=_key("replacement")).value == "按需说明细节"
    ui.session_state["fail-save"] = False
    _button(ui, "save").click().run()
    assert _saved(ui)["rules"][0]["replacement"] == "按需说明细节"
    assert ui.session_state["disk"]["workflow-control-text-previous:fixture:sft"] == saved
    assert not ui.exception

def test_previous_version_survives_reload_and_requires_confirmed_restore():
    ui = AppTest.from_string(SCRIPT).run()
    _initial_save(ui)
    first = deepcopy(_saved(ui))
    _button(ui, "edit").click().run()
    ui.text_input(key=_key("marker")).set_value("[[SECOND_VERSION]]").run()
    _button(ui, "save").click().run()
    second = deepcopy(_saved(ui))
    restored = AppTest.from_string(SCRIPT)
    restored.session_state["workflow-form-draft:fixture"] = deepcopy(ui.session_state["disk"])
    restored.session_state["ui_language"] = "en"
    restored.run()
    _button(restored, "previous").click().run()
    assert _saved(restored) == second
    assert restored.text_input(key=_key("marker")).value == first["rules"][0]["marker"]
    _button(restored, "save").click().run()
    assert _saved(restored) == first
    assert restored.session_state["disk"]["workflow-control-text-previous:fixture:sft"] == second
    assert not restored.exception

def test_node_switch_and_reset_keep_unsaved_changes_out_of_tasks():
    ui = AppTest.from_string(SCRIPT).run()
    _initial_save(ui)
    first = deepcopy(_saved(ui))
    _button(ui, "edit").click().run()
    ui.text_input(key=_key("marker")).set_value("[[SFT_CONTROL]]").run()
    ui.radio(key="fixture-node").set_value("multiturn").run()
    assert "multiturn" not in ui.session_state["snapshot"]
    assert ui.text_input(key=_key("marker", node="multiturn")).disabled
    ui.radio(key="fixture-node").set_value("sft").run()
    assert ui.text_input(key=_key("marker")).value == "[[SFT_CONTROL]]"
    _button(ui, "restore").click().run()
    assert not ui.toggle(key=_key("enabled", rule="all")).value
    assert _saved(ui) == first
    _button(ui, "cancel").click().run()
    assert _saved(ui) == first

def test_legacy_configuration_only_upgrades_when_saved():
    from lib.contexts.datasets.api import default_control_text_config
    old = default_control_text_config()
    old["schema"] = "shujian.control-text-adaptation/v1"
    ui = AppTest.from_string(SCRIPT)
    ui.session_state["workflow-form-draft:fixture"] = {"workflow-control-text:fixture:sft": old}
    ui.run()
    assert ui.warning
    assert ui.code[0].value.startswith("[[SJ_OUTPUT_VERBOSITY:5]]")
    _button(ui, "edit").click().run()
    assert _saved(ui) == old
    assert ui.code[0].value.startswith("Desired oververbosity 5")
    _button(ui, "cancel").click().run()
    assert _saved(ui) == old
    _button(ui, "edit").click().run()
    _button(ui, "save").click().run()
    assert _saved(ui)["schema"].endswith("/v2")
    assert ui.session_state["disk"]["workflow-control-text-previous:fixture:sft"] == old
