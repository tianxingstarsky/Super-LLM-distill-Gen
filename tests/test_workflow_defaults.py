"""The settings page only presents defaults that affect normal workflows."""
from pathlib import Path

from streamlit.testing.v1 import AppTest


def _defaults_ui(language="zh"):
    source = (Path(__file__).resolve().parents[1] / "configs/preferences.yaml").read_text(encoding="utf-8")
    script = '''
import streamlit as st
from lib.application.generation_settings_service import GenerationSettingsApplication
from lib.presentation.streamlit.generation_settings_page import render_workflow_defaults
from lib.presentation.streamlit.i18n import install_streamlit_localization
st.session_state['ui_language'] = LANGUAGE
install_streamlit_localization()
st.session_state.setdefault('document', SOURCE)
class MemoryPort:
    def read(self, category): return st.session_state['document']
    def compare_and_swap(self, category, expected, replacement):
        if st.session_state.get('fail-save'):
            raise OSError('memory port refused the update')
        assert expected == st.session_state['document']
        st.session_state['document'] = replacement
app = GenerationSettingsApplication(MemoryPort())
render_workflow_defaults(app)
st.session_state['summary'] = app.summary(app.load('生成偏好'))
'''.replace("SOURCE", repr(source)).replace("LANGUAGE", repr(language))
    return AppTest.from_string(script).run()


def _format_note(ui):
    return next(element.proto.body for element in ui.get("html")
                if 'class="df-settings-format-note"' in element.proto.body)


def test_normal_defaults_do_not_expose_command_sampling_and_preserve_it_on_save():
    ui = _defaults_ui()
    assert not ui.exception
    before = ui.session_state["summary"]
    assert not ui.slider and not ui.number_input
    assert len(ui.selectbox) == 1
    ui.selectbox(key="workflow-default-sft-style").set_value("drop").run()
    ui.button(key="workflow-defaults-save").click().run()
    assert not ui.exception
    after = ui.session_state["summary"]
    assert after["cot_style"] == "drop"
    assert {key: value for key, value in before.items() if key != "cot_style"} == {
        key: value for key, value in after.items() if key != "cot_style"}


def test_workflow_default_status_tracks_unsaved_changes_and_successful_save():
    ui = _defaults_ui()
    assert not ui.exception
    before = ui.session_state["summary"]
    assert 'data-state="saved"' in _format_note(ui)
    current = ui.selectbox(key="workflow-default-sft-style").value
    changed = "drop" if current == "separated" else "separated"
    ui.selectbox(key="workflow-default-sft-style").set_value(changed).run()
    assert not ui.exception
    assert 'data-state="changed"' in _format_note(ui)
    assert "未保存修改" in _format_note(ui)
    assert ui.session_state["summary"] == before
    ui.button(key="workflow-defaults-save").click().run()
    assert not ui.exception
    assert 'data-state="saved"' in _format_note(ui)
    assert "当前默认值" in _format_note(ui)
    assert ui.session_state["summary"]["cot_style"] == changed
    ui.run()
    assert 'data-state="saved"' in _format_note(ui)


def test_failed_default_save_retains_unsaved_status_and_current_preferences():
    ui = _defaults_ui()
    before = ui.session_state["summary"]
    current = ui.selectbox(key="workflow-default-sft-style").value
    ui.selectbox(key="workflow-default-sft-style").set_value(
        "drop" if current == "separated" else "separated").run()
    ui.session_state["fail-save"] = True
    ui.button(key="workflow-defaults-save").click().run()
    assert not ui.exception
    assert 'data-state="changed"' in _format_note(ui)
    assert ui.session_state["summary"] == before
    assert len(ui.error) == 1 and "默认值未能保存" in ui.error[0].value
    assert not ui.toast


def test_english_default_status_and_format_description_are_localized():
    ui = _defaults_ui("en")
    assert not ui.exception
    assert "Current default" in _format_note(ui)
    current = ui.selectbox(key="workflow-default-sft-style").value
    changed = "drop" if current == "separated" else "separated"
    ui.selectbox(key="workflow-default-sft-style").set_value(changed).run()
    assert not ui.exception
    assert "Unsaved change" in _format_note(ui)
    expected = ("Training files keep only the final answer." if changed == "drop" else
                "Reasoning and final answers are saved in separate fields.")
    assert expected in _format_note(ui)
    assert "未保存" not in _format_note(ui)
    assert any("Existing runs keep their settings." in item.value for item in ui.caption)
