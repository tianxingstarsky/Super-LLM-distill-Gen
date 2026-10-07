"""Search connections stay in the input area and do not leak into drafts."""
from streamlit.testing.v1 import AppTest


SCRIPT = '''
import streamlit as st
from lib.presentation.streamlit.workflow_page import _draft_web_control
from lib.presentation.streamlit.i18n import install_streamlit_localization
st.session_state.setdefault('ws', 'fixture')
st.session_state.setdefault('checks', 0)
install_streamlit_localization()
class Application:
    def save_web_research_connection(self, value):
        if st.session_state.get('fail-save'):
            raise ValueError(value)
        st.session_state['fake-connection'] = value
        st.session_state['fake-revision'] = st.session_state.get('fake-revision', 0) + 1
    def check_web_research_connection(self):
        st.session_state['checks'] = st.session_state.get('checks', 0) + 1
        return 'ready'
configured = bool(st.session_state.get('fake-connection'))
_draft_web_control('fixture', Application(), configured=configured,
    connection={'brave_configured': configured, 'key_source': 'local' if configured else 'none',
                'connection_revision': st.session_state.get('fake-revision', 0)})
'''.encode('ascii', 'backslashreplace').decode('ascii')


def visible(ui):
    return '\n'.join(str(item.value) for item in
                     [*ui.get('html'), *ui.caption, *ui.success, *ui.warning, *ui.error])


def test_save_connection_clears_password_without_search_or_draft_consent():
    ui = AppTest.from_string(SCRIPT).run()
    assert ui.button(key='workflow-web-check:fixture').disabled
    assert not ui.button(key='workflow-web-save:fixture').disabled
    ui.button(key='workflow-web-save:fixture').click().run()
    assert ui.warning and 'fake-connection' not in ui.session_state
    secret = 'synthetic-search-key'
    ui.text_input(key='workflow-web-api-key:fixture').set_value(secret).run()
    ui.button(key='workflow-web-save:fixture').click().run()
    assert not ui.exception
    assert ui.session_state['fake-connection'] == secret
    assert ui.text_input(key='workflow-web-api-key:fixture').value == ''
    assert secret not in visible(ui)
    assert ui.session_state['checks'] == 0
    assert not ui.checkbox(key='workflow-web-research-enabled:fixture').value
    assert not ui.button(key='workflow-web-check:fixture').disabled
    ui.button(key='workflow-web-check:fixture').click().run()
    assert ui.session_state['checks'] == 1
    assert any('连接可用' in item.value for item in ui.success)
    ui.text_input(key='workflow-web-api-key:fixture').set_value('replacement-test-key').run()
    ui.button(key='workflow-web-save:fixture').click().run()
    assert not any('连接可用' in item.value for item in ui.success)
    assert ui.session_state['checks'] == 1
    assert ui.text_input(key='workflow-web-api-key:fixture').value == ''


def test_external_connection_revision_invalidates_old_probe():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['fake-connection'] = 'synthetic-search-key'
    ui.run()
    ui.button(key='workflow-web-save:fixture').click().run()
    assert ui.session_state['fake-connection'] == 'synthetic-search-key'
    ui.button(key='workflow-web-check:fixture').click().run()
    assert any('连接可用' in item.value for item in ui.success)
    ui.session_state['fake-revision'] = 2
    ui.run()
    assert not any('连接可用' in item.value for item in ui.success)


def test_save_error_is_safe_and_keeps_existing_topics():
    ui = AppTest.from_string(SCRIPT).run()
    ui.checkbox(key='workflow-web-research-enabled:fixture').check().run()
    ui.text_input(key='workflow-web-research-query:fixture').set_value('equipment maintenance').run()
    ui.session_state['fail-save'] = True
    secret = 'synthetic-error-key'
    ui.text_input(key='workflow-web-api-key:fixture').set_value(secret).run()
    ui.button(key='workflow-web-save:fixture').click().run()
    assert not ui.exception and ui.error
    assert secret not in visible(ui)
    assert ui.text_input(key='workflow-web-api-key:fixture').value == ''
    assert ui.text_input(key='workflow-web-research-query:fixture').value == 'equipment maintenance'
    assert ui.session_state['checks'] == 0


def test_english_connection_controls_have_english_feedback():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['ui_language'] = 'en'
    ui.run()
    assert ui.text_input(key='workflow-web-api-key:fixture').label == 'Brave Search API key'
    ui.text_input(key='workflow-web-api-key:fixture').set_value('synthetic-search-key').run()
    ui.button(key='workflow-web-save:fixture').click().run()
    assert not ui.exception
    assert any('Search connection saved.' in item.value for item in ui.success)
    assert ui.button(key='workflow-web-save:fixture').label == 'Save search connection'
