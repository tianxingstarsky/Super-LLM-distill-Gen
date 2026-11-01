"""Explicit edits cannot corrupt the saved rules or their recovery copy."""
from copy import deepcopy
import pytest
from streamlit.testing.v1 import AppTest

@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield

SCRIPT = '''
import streamlit as st
from copy import deepcopy
from lib.domain.creation_draft import validate_creation_draft
from lib.domain.creation_draft import CreationDraftConflict
from lib.presentation.streamlit.workflow_page import _commit_draft_fields
from lib.presentation.streamlit.workflow_control_text_settings import render_control_text_settings, control_text_snapshot
class DraftStore:
    def load(self):
        return st.session_state.get("disk", {})
    def compare_update(self, changes, expected):
        current = self.load()
        if any((key in current if value is None else current.get(key) != value)
               for key, value in expected.items()):
            raise CreationDraftConflict()
        self.replace({**current, **changes})
        return self.load()
    def replace(self, values):
        if st.session_state.get("fail-save"):
            raise OSError("fixture unavailable")
        st.session_state["disk"] = validate_creation_draft(values)
st.session_state["workflow-draft-application:fixture"] = DraftStore()
if "disk" not in st.session_state:
    st.session_state["disk"] = deepcopy(st.session_state.get("workflow-form-draft:fixture", {}))
node = st.radio("Node", ("sft", "multiturn", "cot"), key="fixture-node")
render_control_text_settings(node, "fixture", save_fields=_commit_draft_fields,
                             suggestions=st.session_state.get("suggestions_app"))
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
    assert ui.code[0].value == "<reason-strength>Desired oververbosity 5</reason-strength>. I will check the evidence before answering."
    _button(ui, "cancel").click().run()
    assert ui.session_state["snapshot"] == {}
    assert ui.text_input(key=_key("marker")).value == "[[SJ_OUTPUT_VERBOSITY:5]]"
    _initial_save(ui)
    assert _saved(ui)["schema"].endswith("/v3")
    assert _saved(ui)["annotation_tag"] == "reason-strength"
    assert _saved(ui)["prefix_char_limit"] == 512
    assert _saved(ui)["review_suggestions"] is True
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


def test_prefix_and_review_controls_save_explicitly_and_do_not_match_the_body():
    ui = AppTest.from_string(SCRIPT).run()
    _initial_save(ui)
    assert ui.number_input(key=_key("prefix_char_limit", rule="all")).disabled
    _button(ui, "edit").click().run()
    ui.number_input(key=_key("prefix_char_limit", rule="all")).set_value(64).run()
    ui.toggle(key=_key("review_suggestions", rule="all")).set_value(False).run()
    assert _saved(ui)["prefix_char_limit"] == 512
    source = "开始检查。" * 16 + "Desired oververbosity 5"
    ui.text_area(key="control-text-ui:fixture:sft:preview").set_value(source).run()
    assert ui.code[0].value == source
    _button(ui, "save").click().run()
    assert _saved(ui)["prefix_char_limit"] == 64
    assert _saved(ui)["review_suggestions"] is False
    assert not ui.exception


def test_rule_save_detects_another_session_and_preserves_both_drafts():
    ui = AppTest.from_string(SCRIPT).run()
    _initial_save(ui)
    _button(ui, "edit").click().run()
    ui.text_input(key=_key("annotation_tag", rule="all")).set_value("local-control").run()
    disk = deepcopy(ui.session_state["disk"])
    disk["workflow-control-text:fixture:sft"]["annotation_tag"] = "other-window"
    ui.session_state["disk"] = disk
    _button(ui, "save").click().run()
    assert _saved(ui)["annotation_tag"] == "other-window"
    assert ui.text_input(key=_key("annotation_tag", rule="all")).value == "local-control"
    assert any("发生变化" in error.value for error in ui.error)
    _button(ui, "cancel").click().run()
    assert ui.text_input(key=_key("annotation_tag", rule="all")).value == "other-window"


def test_reviewer_suggestion_is_only_applied_after_explicit_save():
    ui = AppTest.from_string(SCRIPT).run()
    _initial_save(ui)
    original = deepcopy(_saved(ui))
    proposal_config = deepcopy(original)
    proposal_config["rules"][0]["matches"].append("Desired detail level 5")

    class Suggestions:
        def __init__(self):
            self.record = {"id": "proposal-1", "run_id": "review-task", "node": "sft", "status": "pending",
                "base_configuration": original, "proposed_configuration": proposal_config,
                "source": {"prefix": "Desired detail level 5. I will check the task."},
                "proposal": {"rule_id": original["rules"][0]["id"], "quote": "Desired detail level 5",
                             "reason": "Possible control wording; confirm its meaning."}}
        def list_pending(self, node, limit=30):
            return [deepcopy(self.record)] if self.record["status"] == "pending" else []
        def get(self, identifier):
            return deepcopy(self.record)
        def resolve(self, identifier, *, status):
            self.record["status"] = status

    suggestions = Suggestions()
    ui.session_state["suggestions_app"] = suggestions
    ui.run()
    ui.button(key="control-text-suggestions-load:fixture:sft").click().run()
    assert _saved(ui) == original and suggestions.record["status"] == "pending"
    assert "Desired detail level 5" in ui.text_area(key=_key("matches")).value
    _button(ui, "cancel").click().run()
    assert _saved(ui) == original and suggestions.record["status"] == "pending"
    ui.button(key="control-text-suggestions-load:fixture:sft").click().run()
    _button(ui, "save").click().run()
    assert _saved(ui) == proposal_config and suggestions.record["status"] == "applied"
    assert ui.session_state["disk"]["workflow-control-text-previous:fixture:sft"] == original
    assert not ui.exception

@pytest.mark.parametrize("version", (1, 2))
def test_legacy_configuration_only_upgrades_when_saved(version):
    from lib.contexts.datasets.api import default_control_text_config
    old = default_control_text_config()
    old["schema"] = f"shujian.control-text-adaptation/v{version}"
    old.pop("annotation_tag")
    old.pop("prefix_char_limit")
    old.pop("review_suggestions")
    ui = AppTest.from_string(SCRIPT)
    ui.session_state["workflow-form-draft:fixture"] = {"workflow-control-text:fixture:sft": old}
    ui.run()
    assert ui.warning if version == 1 else ui.info
    assert ui.code[0].value.startswith("[[SJ_OUTPUT_VERBOSITY:5]]" if version == 1 else "Desired oververbosity 5")
    _button(ui, "edit").click().run()
    assert _saved(ui) == old
    assert ui.code[0].value.startswith("<reason-strength>Desired oververbosity 5</reason-strength>")
    _button(ui, "cancel").click().run()
    assert _saved(ui) == old
    _button(ui, "edit").click().run()
    _button(ui, "save").click().run()
    assert _saved(ui)["schema"].endswith("/v3")
    assert ui.session_state["disk"]["workflow-control-text-previous:fixture:sft"] == old


def test_wrapper_tag_is_editable_and_bad_preview_never_crashes_or_rewrites_saved_rules():
    ui = AppTest.from_string(SCRIPT).run()
    _initial_save(ui)
    saved = deepcopy(_saved(ui))
    _button(ui, "edit").click().run()
    ui.text_input(key=_key("annotation_tag", rule="all")).set_value("<bad>").run()
    assert ui.error and _button(ui, "save").disabled
    assert _saved(ui) == saved
    ui.text_input(key=_key("annotation_tag", rule="all")).set_value("training-control").run()
    assert ui.code[0].value.startswith("<training-control>Desired oververbosity 5</training-control>")
    ui.text_area(key="control-text-ui:fixture:sft:preview").set_value(
        "<training-control>Desired oververbosity 5").run()
    assert ui.error and not ui.exception
    assert _saved(ui) == saved
    ui.text_area(key="control-text-ui:fixture:sft:preview").set_value("Desired oververbosity 5").run()
    _button(ui, "save").click().run()
    assert _saved(ui)["annotation_tag"] == "training-control"
    assert ui.text_input(key=_key("annotation_tag", rule="all")).disabled
