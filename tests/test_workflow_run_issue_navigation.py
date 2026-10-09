"""A failed-run message opens real evidence without resuming the workflow."""
import re

import pytest
from streamlit.testing.v1 import AppTest


SCRIPT = '''
from copy import deepcopy
import streamlit as st
from lib.presentation.streamlit.workflow_page import render_run
from lib.presentation.streamlit import workflow_canvas
from lib.presentation.streamlit.i18n import install_streamlit_localization
install_streamlit_localization()
st.session_state.setdefault('ui_language', 'zh')
st.session_state['ws'] = 'fixture'
st.session_state.setdefault('fixture-state', {
    'name': 'Preserved failed workflow', 'status': 'failed', 'targets': ['sft'],
    'error': 'model_stream_interrupted',
    'stages': {
        'ingest': {'label': '输入解析', 'status': 'completed', 'done': 2, 'total': 2},
        'sft': {'label': 'SFT 生成', 'status': 'failed', 'done': 1, 'total': 2,
                'error': 'model_stream_interrupted'},
        'package': {'label': '质检打包', 'status': 'pending', 'done': 0, 'total': 2},
    },
    'events': [{'stage': 'sft', 'kind': 'run_failed', 'at': '2026-10-09T10:00:00Z'}],
})
st.session_state.setdefault('fixture-recipe', {
    'targets': ['sft'], 'sources': [], 'model': 'preserved-model',
    'node_models': {'sft': {'generation': {'backend': 'saved-service', 'model': 'preserved-model',
                                         'context_window_tokens': 131072, 'max_output_tokens': 32768}}},
})
st.session_state.setdefault('workflow-stage:fixture', 'ingest')
st.session_state.setdefault('workflow-follow:fixture', False)
def canvas(**kwargs):
    st.session_state['fixture-selected'] = kwargs['spec']['selected']
    return None
workflow_canvas._canvas = canvas
class Application:
    def state(self, run_id): return deepcopy(st.session_state['fixture-state'])
    def recipe(self, run_id): return deepcopy(st.session_state['fixture-recipe'])
    def is_active(self, run_id): return False
    def read_streams(self, run_id, *, stage):
        st.session_state['fixture-stream-stage'] = stage
        return []
def begin(args):
    st.session_state.setdefault('fixture-begin-calls', []).append(args)
render_run(Application(), 'fixture', begin)
'''


def _scroll_scripts(ui):
    return [item.proto.body for item in ui.get('html')
            if 'const selector=' in item.proto.body and 'workflow-run-canvas' in item.proto.body]


def test_failed_message_opens_failed_node_with_saved_error_and_output_without_execution():
    ui = AppTest.from_string(SCRIPT).run()
    assert not ui.exception
    before_state = ui.session_state['fixture-state']
    before_recipe = ui.session_state['fixture-recipe']
    action = ui.button(key='workflow-run-failure:fixture')
    assert '模型输出连接中断' in action.label
    assert not _scroll_scripts(ui)
    action.click().run()
    assert not ui.exception
    assert ui.session_state['workflow-stage:fixture'] == 'sft'
    assert ui.session_state['fixture-selected'] == 'sft'
    assert ui.session_state['canvas-open:live-canvas:fixture'] is True
    assert not ui.toggle(key='workflow-follow:fixture').value
    assert ui.session_state['fixture-stream-stage'] == 'sft'
    assert any('节点错误：' in item.value for item in ui.error)
    assert len(_scroll_scripts(ui)) == 1
    assert 'fixture-begin-calls' not in ui.session_state
    assert ui.session_state['fixture-state'] == before_state
    assert ui.session_state['fixture-recipe'] == before_recipe
    # The normal fragment refresh keeps the evidence open and does not scroll again.
    ui.run()
    assert not ui.exception
    assert ui.session_state['fixture-selected'] == 'sft'
    assert not _scroll_scripts(ui)
    assert 'fixture-begin-calls' not in ui.session_state


@pytest.mark.parametrize('has_error', [True, False])
def test_failure_navigation_uses_error_node_then_current_selection_when_no_stage_is_failed(has_error):
    ui = AppTest.from_string(SCRIPT).run()
    state = ui.session_state['fixture-state']
    state['stages']['sft']['status'] = 'pending'
    if not has_error:
        state['stages']['sft'].pop('error')
    ui.session_state['fixture-state'] = state
    ui.run()
    ui.button(key='workflow-run-failure:fixture').click().run()
    assert not ui.exception
    expected = 'sft' if has_error else 'ingest'
    assert ui.session_state['fixture-selected'] == expected
    assert ui.session_state['fixture-stream-stage'] == expected
    assert 'fixture-begin-calls' not in ui.session_state


def test_failed_stage_takes_priority_over_older_node_error_and_pauses_existing_follow():
    ui = AppTest.from_string(SCRIPT).run()
    state = ui.session_state['fixture-state']
    state['stages']['ingest']['error'] = 'older-source-error'
    ui.session_state['fixture-state'] = state
    ui.toggle(key='workflow-follow:fixture').set_value(True).run()
    ui.button(key='workflow-run-failure:fixture').click().run()
    assert not ui.exception
    assert ui.session_state['fixture-selected'] == 'sft'
    assert not ui.toggle(key='workflow-follow:fixture').value
    assert 'fixture-begin-calls' not in ui.session_state


def test_english_failure_message_keeps_error_translation_when_opening_evidence():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['ui_language'] = 'en'
    ui.run()
    action = ui.button(key='workflow-run-failure:fixture')
    assert not re.search(r'[\u3400-\u9fff]', action.label)
    assert 'model stream disconnected' in action.label.lower()
    action.click().run()
    assert not ui.exception
    assert ui.session_state['fixture-selected'] == 'sft'
    assert 'fixture-begin-calls' not in ui.session_state
