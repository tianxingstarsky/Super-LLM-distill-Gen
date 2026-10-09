"""Edit preferences through an injected application and memory port."""
from pathlib import Path
import pytest
import yaml
from streamlit.testing.v1 import AppTest


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    """Preference rendering uses an in-memory port and no model requests."""
    yield


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


def _workflow_defaults_ui(saved_style="separated", legacy_style=None):
    source = (Path(__file__).resolve().parents[1] / 'configs/preferences.yaml').read_text(encoding='utf-8')
    document = yaml.safe_load(source)
    document['cot']['style'] = saved_style
    script = '''
import streamlit as st
from lib.application.generation_settings_service import GenerationSettingsApplication
from lib.presentation.streamlit.generation_settings_page import render_workflow_defaults
from lib.presentation.streamlit.i18n import install_streamlit_localization
st.session_state.setdefault('document', SOURCE)
st.session_state.setdefault('ui_language', 'zh')
st.session_state.setdefault('save-count', 0)
legacy_style = LEGACY
if legacy_style is not None and not st.session_state.get('legacy-seeded'):
    st.session_state['workflow-default-sft-style'] = legacy_style
    st.session_state['legacy-seeded'] = True
install_streamlit_localization()
def change_language():
    st.session_state['ui_language'] = st.session_state['test-language']
st.selectbox('Language', ('zh', 'en'), key='test-language', on_change=change_language)
visible = st.checkbox('Show settings', value=True, key='show-settings')
class MemoryPort:
    def read(self, category): return st.session_state['document']
    def compare_and_swap(self, category, expected, replacement):
        assert st.session_state['document'] == expected
        st.session_state['document'] = replacement
        st.session_state['save-count'] += 1
application = GenerationSettingsApplication(MemoryPort())
if visible:
    render_workflow_defaults(application)
st.session_state['saved-style'] = application.summary(application.load('生成偏好'))['cot_style']
'''.replace('SOURCE', repr(yaml.safe_dump(document, allow_unicode=True))).replace('LEGACY', repr(legacy_style))
    return AppTest.from_string(script).run()


def _default_format_note(ui):
    return next(element.proto.body for element in ui.get('html')
                if 'class="df-settings-format-note"' in element.proto.body)


@pytest.mark.parametrize('saved_style', ['separated', 'drop'])
def test_workflow_default_language_switch_recreates_label_without_changing_selection(saved_style):
    ui = _workflow_defaults_ui(saved_style)
    assert not ui.exception
    old_widget = ui.selectbox(key='workflow-default-sft-style:zh')
    old_id = old_widget.id
    assert old_widget.value == saved_style
    before = ui.session_state['document']

    ui.selectbox(key='test-language').set_value('en').run()
    assert not ui.exception
    current = ui.selectbox(key='workflow-default-sft-style:en')
    assert current.id != old_id
    assert current.value == saved_style
    assert current.label == 'Default SFT training format'
    assert current.options == ['Keep reasoning in a separate field', 'Answer only']
    assert 'data-state="saved"' in _default_format_note(ui)
    assert ui.session_state['document'] == before
    assert ui.session_state['save-count'] == 0

    ui.run()  # Opening/dismissing the option menu must not imply a new selection.
    assert ui.selectbox(key='workflow-default-sft-style:en').value == saved_style
    ui.selectbox(key='test-language').set_value('zh').run()
    assert not ui.exception
    assert ui.selectbox(key='workflow-default-sft-style:zh').value == saved_style
    assert 'data-state="saved"' in _default_format_note(ui)
    assert ui.session_state['document'] == before


def test_unsaved_default_survives_language_switches_and_widget_cleanup_until_save():
    ui = _workflow_defaults_ui()
    before = ui.session_state['document']
    ui.selectbox(key='workflow-default-sft-style:zh').set_value('drop').run()
    ui.selectbox(key='test-language').set_value('en').run()
    assert not ui.exception
    assert ui.selectbox(key='workflow-default-sft-style:en').value == 'drop'
    assert 'Unsaved change' in _default_format_note(ui)

    ui.checkbox(key='show-settings').uncheck().run()
    ui.run()  # Let Streamlit discard the absent display widget's state.
    ui.selectbox(key='test-language').set_value('zh').run()
    ui.checkbox(key='show-settings').check().run()
    assert not ui.exception
    assert ui.selectbox(key='workflow-default-sft-style:zh').value == 'drop'
    assert 'data-state="changed"' in _default_format_note(ui)
    assert ui.session_state['document'] == before
    assert ui.session_state['saved-style'] == 'separated'
    assert ui.session_state['save-count'] == 0

    ui.button(key='workflow-defaults-save').click().run()
    assert not ui.exception
    assert ui.session_state['saved-style'] == 'drop'
    assert ui.session_state['save-count'] == 1
    assert 'data-state="saved"' in _default_format_note(ui)
    ui.selectbox(key='test-language').set_value('en').run()
    assert ui.selectbox(key='workflow-default-sft-style:en').value == 'drop'
    assert 'data-state="saved"' in _default_format_note(ui)


def test_existing_session_unsaved_default_is_migrated_to_language_scoped_widget():
    ui = _workflow_defaults_ui(legacy_style='drop')
    assert not ui.exception
    assert ui.selectbox(key='workflow-default-sft-style:zh').value == 'drop'
    assert ui.session_state['saved-style'] == 'separated'
    assert ui.session_state['save-count'] == 0
    assert 'data-state="changed"' in _default_format_note(ui)


def test_clean_default_draft_follows_preferences_saved_elsewhere():
    ui = _workflow_defaults_ui()
    document = yaml.safe_load(ui.session_state['document'])
    document['cot']['style'] = 'drop'
    ui.session_state['document'] = yaml.safe_dump(document, allow_unicode=True)
    ui.run()
    assert not ui.exception
    assert ui.selectbox(key='workflow-default-sft-style:zh').value == 'drop'
    assert 'data-state="saved"' in _default_format_note(ui)
    assert ui.session_state['save-count'] == 0
