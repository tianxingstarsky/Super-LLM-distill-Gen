"""The settings page only presents defaults that affect normal workflows."""
from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_normal_defaults_do_not_expose_command_sampling_and_preserve_it_on_save():
    source = (Path(__file__).resolve().parents[1] / "configs/preferences.yaml").read_text(encoding="utf-8")
    script = '''
import streamlit as st
from lib.application.generation_settings_service import GenerationSettingsApplication
from lib.presentation.streamlit.generation_settings_page import render_workflow_defaults
st.session_state.setdefault('document', SOURCE)
class MemoryPort:
    def read(self, category): return st.session_state['document']
    def compare_and_swap(self, category, expected, replacement):
        assert expected == st.session_state['document']
        st.session_state['document'] = replacement
app = GenerationSettingsApplication(MemoryPort())
render_workflow_defaults(app)
st.session_state['summary'] = app.summary(app.load('生成偏好'))
'''.replace("SOURCE", repr(source))
    ui = AppTest.from_string(script).run()
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
