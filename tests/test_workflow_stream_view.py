"""Live output stays scoped to a node and never treats fragments as samples."""
from streamlit.testing.v1 import AppTest


SCRIPT = '''
import streamlit as st
from lib.presentation.streamlit.workflow_stream_view import render_stream_output
from lib.presentation.streamlit.i18n import install_streamlit_localization
install_streamlit_localization()
class Application:
    def read_streams(self, run_id):
        if st.session_state.get('read-failed'):
            raise ValueError('untrusted raw provider error')
        return st.session_state.get('rows', [])
render_stream_output(Application(), 'fixture', 'sft')
'''


def row(request_id, **values):
    return dict(id=request_id, stage='sft', unit='unit-1', role='generation',
                status='active', text='{"answer": "partial', reasoning='',
                updated_at='2026-10-07 08:30:00 UTC', **values)


def test_no_stream_does_not_render_an_empty_panel():
    ui = AppTest.from_string(SCRIPT).run()
    assert not ui.exception and not ui.code and not ui.caption and not ui.get('html')


def test_live_preview_filters_nodes_and_keeps_untrusted_output_literal():
    ui = AppTest.from_string(SCRIPT)
    first = row('active-1')
    first['text'] = '<script>alert(1)</script> {"answer": "partial'
    ui.session_state['rows'] = [first, dict(row('other'), stage='preference', text='OTHER NODE')]
    ui.run()
    assert not ui.exception
    assert [code.value for code in ui.code] == [first['text']]
    assert not ui.button


def test_interrupted_preview_is_not_described_as_completed_training_data():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['rows'] = [dict(row('partial-1'), status='interrupted', truncated=True)]
    ui.run()
    assert not ui.exception and ui.warning
    assert '此片段未进入训练数据' in ui.warning[0].value
    assert any('最近的片段' in item.value for item in ui.caption)


def test_concurrent_requests_can_be_selected_without_mixing_text():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['rows'] = [row('active-2'), dict(row('done-1'), status='completed', text='COMPLETE')]
    ui.run()
    assert ui.code[0].value == '{"answer": "partial'
    ui.toggle[0].set_value(False).run()
    ui.selectbox[0].set_value('done-1').run()
    assert ui.code[0].value == 'COMPLETE'
    ui.session_state['rows'] = [row('replacement')]
    ui.run()
    assert not ui.exception and ui.code[0].value == '{"answer": "partial'


def test_english_stream_feedback_and_read_failure_are_safe():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['ui_language'] = 'en'
    ui.session_state['rows'] = [dict(row('partial-1'), status='interrupted')]
    ui.run()
    assert 'excluded from training data' in ui.warning[0].value
    assert any(item.value == 'Interrupted. Retry available.' for item in ui.caption)
    ui.session_state['read-failed'] = True
    ui.run()
    assert not ui.exception and not ui.code
    assert ui.caption[0].value.startswith('Live output is temporarily unavailable.')
    assert not any('untrusted raw' in item.value for item in ui.caption)
