"""Edit preferences through an injected application and memory port."""
from pathlib import Path
import pytest
from streamlit.testing.v1 import AppTest


@pytest.mark.parametrize('save_button', ['pref-save-form', 'pref-save-bottom'])
def test_generation_editor_keeps_controls_visible_and_saves_through_injected_application(save_button):
    source = (Path(__file__).resolve().parents[1] / 'configs/preferences.yaml').read_text(encoding='utf-8')
    script = '''
import streamlit as st
from lib.application.generation_settings_service import GenerationSettingsApplication
from lib.presentation.streamlit.generation_settings_page import render_generation_settings
st.session_state.setdefault('document', SOURCE)
class MemoryPort:
    def read(self, category): return st.session_state['document']
    def compare_and_swap(self, category, expected, replacement):
        assert st.session_state['document'] == expected
        st.session_state['document'] = replacement
application = GenerationSettingsApplication(MemoryPort())
render_generation_settings(application)
st.session_state['saved_templates'] = application.summary(application.load('生成偏好'))['templates_per_dim']
'''.replace('SOURCE', repr(source))
    ui = AppTest.from_string(script.encode('ascii','backslashreplace').decode('ascii')).run()
    assert not ui.exception
    assert [item.label for item in ui.expander] == ['高级编辑：YAML 原始配置']
    assert not ui.expander[0].slider
    assert not ui.expander[0].number_input
    assert ui.selectbox(key='pref-cot-style').value
    assert ui.button(key='pref-save-form')
    assert ui.button(key='pref-save-bottom')
    previous = ui.number_input(key='pref-templates').value
    ui.number_input(key='pref-templates').set_value(previous + 1).run()
    assert ui.session_state['saved_templates'] == previous
    ui.button(key=save_button).click().run()
    assert ui.session_state['saved_templates'] == previous + 1
    assert not ui.exception
