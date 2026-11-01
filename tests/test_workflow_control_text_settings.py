"""Node text rules remain editable, persisted and isolated without model calls."""
from copy import deepcopy

import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """This form uses local text adaptation only."""
    yield


SCRIPT = '''
from copy import deepcopy
import streamlit as st
from lib.presentation.streamlit.workflow_control_text_settings import (
    render_control_text_settings, control_text_snapshot,
)

def save_field(workspace, key):
    draft_key = f"workflow-form-draft:{workspace}"
    st.session_state[draft_key] = {
        **st.session_state.get(draft_key, {}), key: deepcopy(st.session_state[key]),
    }

node = st.radio("Node", ("sft", "multiturn", "cot"), key="fixture-node")
render_control_text_settings(node, "fixture", save_field=save_field)
try:
    st.session_state["snapshot"] = control_text_snapshot("fixture", ("sft", "multiturn", "cot"))
    st.session_state["snapshot_error"] = ""
except ValueError as error:
    st.session_state["snapshot_error"] = str(error)
'''


def _key(field, *, node="sft", rule=0):
    return f"control-text-ui:fixture:{node}:{rule}:{field}"


def _saved(ui, node="sft"):
    return ui.session_state["workflow-form-draft:fixture"][f"workflow-control-text:fixture:{node}"]


def test_opening_is_read_only_and_edits_save_complete_rule_with_local_preview():
    ui = AppTest.from_string(SCRIPT).run()
    assert not ui.exception
    assert ui.session_state["snapshot"] == {}
    assert not ui.toggle(key=_key("enabled", rule="all")).value
    ui.toggle(key=_key("enabled", rule="all")).set_value(True).run()
    ui.text_input(key=_key("id")).set_value("my-verbosity").run()
    ui.text_area(key=_key("matches")).set_value("Desired oververbosity 5\ncustom verbosity").run()
    ui.text_input(key=_key("marker")).set_value("[[MY_VERBOSITY]]").run()
    ui.radio(key=_key("mode")).set_value("replace").run()
    assert ui.error  # Incomplete target is retained until the user finishes it.
    ui.text_area(key=_key("replacement")).set_value("Thinking is enabled").run()
    assert not ui.exception
    assert not ui.error
    saved = _saved(ui)
    assert saved["rules"][0] == {
        "id": "my-verbosity", "enabled": True,
        "matches": ["Desired oververbosity 5", "custom verbosity"],
        "marker": "[[MY_VERBOSITY]]", "replacement": "Thinking is enabled",
    }
    assert "field" not in saved["rules"][0]
    assert "observed_value" not in saved["rules"][0]
    assert ui.session_state["snapshot"]["sft"] == saved
    assert any("Thinking is enabled. I will" in code.value for code in ui.code)
    assert any("[[MY_VERBOSITY]]. I will" in code.value for code in ui.code)


def test_master_off_keeps_edits_and_restore_does_not_turn_it_on():
    ui = AppTest.from_string(SCRIPT).run()
    ui.toggle(key=_key("enabled", rule="all")).set_value(True).run()
    ui.toggle(key=_key("enabled", rule="all")).set_value(False).run()
    ui.text_input(key=_key("marker")).set_value("[[MY_DISABLED_RULE]]").run()
    assert not _saved(ui)["enabled"]
    assert _saved(ui)["rules"][0]["marker"] == "[[MY_DISABLED_RULE]]"
    assert ui.code[0].value.startswith("Desired oververbosity 5.")
    ui.button(key="control-text-ui:fixture:sft:restore").click().run()
    assert not _saved(ui)["enabled"]
    assert _saved(ui)["rules"][0]["marker"] == "[[SJ_OUTPUT_VERBOSITY:5]]"
    ui.button(key="control-text-ui:fixture:sft:add").click().run()
    assert len(_saved(ui)["rules"]) == 3
    ui.button(key="control-text-ui:fixture:sft:remove").click().run()
    assert len(_saved(ui)["rules"]) == 2
    assert not ui.exception


def test_node_switch_and_reloaded_draft_preserve_separate_rules():
    ui = AppTest.from_string(SCRIPT).run()
    ui.text_input(key=_key("marker")).set_value("[[SFT_CONTROL]]").run()
    ui.radio(key="fixture-node").set_value("multiturn").run()
    assert "multiturn" not in ui.session_state["snapshot"]
    ui.text_input(key=_key("marker", node="multiturn")).set_value("[[DIALOGUE_CONTROL]]").run()
    ui.radio(key="fixture-node").set_value("sft").run()
    assert ui.text_input(key=_key("marker")).value == "[[SFT_CONTROL]]"
    restored = AppTest.from_string(SCRIPT)
    restored.session_state["workflow-form-draft:fixture"] = deepcopy(ui.session_state["workflow-form-draft:fixture"])
    restored.session_state["ui_language"] = "en"
    restored.run()
    assert not restored.exception
    assert restored.text_input(key=_key("marker")).value == "[[SFT_CONTROL]]"
    assert restored.toggle(key=_key("enabled", rule="all")).label == "Enable output text adaptation"
    restored.radio(key="fixture-node").set_value("multiturn").run()
    assert restored.text_input(key=_key("marker", node="multiturn")).value == "[[DIALOGUE_CONTROL]]"


def test_invalid_rule_is_saved_and_blocks_snapshot_until_disabled_or_fixed():
    ui = AppTest.from_string(SCRIPT).run()
    ui.toggle(key=_key("enabled", rule="all")).set_value(True).run()
    ui.text_input(key=_key("marker")).set_value("").run()
    assert not ui.exception
    assert ui.error
    assert _saved(ui)["rules"][0]["marker"] == ""
    assert ui.session_state["snapshot_error"].startswith("sft:")
    ui.radio(key="fixture-node").set_value("cot").run()
    ui.radio(key="fixture-node").set_value("sft").run()
    assert ui.text_input(key=_key("marker")).value == ""
    ui.toggle(key=_key("enabled")).set_value(False).run()
    assert not ui.session_state["snapshot_error"]
    assert not _saved(ui)["rules"][0]["enabled"]
    assert _saved(ui)["rules"][0]["marker"] == ""
