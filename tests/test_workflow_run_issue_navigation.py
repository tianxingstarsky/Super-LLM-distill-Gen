"""A failed-run message opens real evidence without resuming the workflow."""
import re
from copy import deepcopy

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
    'node_prompt_templates': {'sft': {'workflow.sft': 'Pinned generation instructions.',
                                     'workflow.jev_score': 'Pinned quality instructions.'}},
})
st.session_state.setdefault('workflow-form-draft:fixture', {'model': 'different-unsaved-model'})
st.session_state.setdefault('workflow-stage:fixture', 'ingest')
st.session_state.setdefault('workflow-follow:fixture', False)
def canvas(**kwargs):
    st.session_state['fixture-selected'] = kwargs['spec']['selected']
    st.session_state['fixture-canvas-spec'] = deepcopy(kwargs['spec'])
    return st.session_state.get('fixture-canvas-event')
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


def _navigation_receipts(ui):
    return [item.proto.body for item in ui.get('html')
            if 'class="df-workflow-setup-reveal"' in item.proto.body]


def test_failed_message_opens_failed_node_with_saved_error_and_output_without_execution():
    ui = AppTest.from_string(SCRIPT).run()
    assert not ui.exception
    before_state = deepcopy(ui.session_state['fixture-state'])
    before_recipe = deepcopy(ui.session_state['fixture-recipe'])
    before_draft = deepcopy(ui.session_state['workflow-form-draft:fixture'])
    action = ui.button(key='workflow-run-failure:fixture')
    assert '模型输出连接中断' in action.label
    assert not _navigation_receipts(ui)
    action.click().run()
    assert not ui.exception
    assert ui.session_state['workflow-stage:fixture'] == 'sft'
    assert ui.session_state['fixture-selected'] == 'sft'
    assert ui.session_state['canvas-open:live-canvas:fixture'] is True
    assert not ui.toggle(key='workflow-follow:fixture').value
    assert ui.session_state['fixture-stream-stage'] == 'sft'
    assert any('节点错误：' in item.value for item in ui.error)
    assert len(_navigation_receipts(ui)) == 1
    spec = ui.session_state['fixture-canvas-spec']
    assert spec['expanded'] is True
    assert spec['inspector']['open'] is True
    assert spec['reveal']['key'].startswith('workflow-run-canvas-')
    assert re.fullmatch(r'[a-zA-Z0-9_-]+', spec['inspector']['key'])
    assert spec['inspector']['key'] != spec['reveal']['key']
    assert any('preserved-model' in item.proto.body and '131,072' in item.proto.body
               for item in ui.get('html'))
    prompt = ui.text_area(key='workflow-run-prompt-body:fixture:sft:workflow.sft')
    assert prompt.disabled and prompt.value == 'Pinned generation instructions.'
    assert 'fixture-begin-calls' not in ui.session_state
    assert ui.session_state['fixture-state'] == before_state
    assert ui.session_state['fixture-recipe'] == before_recipe
    assert ui.session_state['workflow-form-draft:fixture'] == before_draft
    # Extra fragment commits retain the explicit request until its bridge receipt.
    ui.run()
    assert not ui.exception
    assert ui.session_state['fixture-selected'] == 'sft'
    assert ui.session_state['fixture-canvas-spec']['reveal'] == spec['reveal']
    assert len(_navigation_receipts(ui)) == 1
    ui.session_state['fixture-canvas-event'] = {'action': 'reveal', 'node': 'sft',
        'request_serial': spec['reveal']['serial'], 'outcome': 'completed', 'serial': 'ack'}
    ui.run()
    assert not ui.exception
    assert 'workflow-run-reveal:fixture' not in ui.session_state
    assert not _navigation_receipts(ui)
    ui.run()
    assert 'reveal' not in ui.session_state['fixture-canvas-spec']
    assert ui.session_state['fixture-canvas-spec']['inspector']['open'] is True
    assert 'fixture-begin-calls' not in ui.session_state
    assert ui.session_state['fixture-state'] == before_state
    assert ui.session_state['fixture-recipe'] == before_recipe
    assert ui.session_state['workflow-form-draft:fixture'] == before_draft


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


def test_later_error_click_is_not_consumed_by_a_stale_or_invalid_bridge_receipt():
    ui = AppTest.from_string(SCRIPT).run()
    ui.button(key='workflow-run-failure:fixture').click().run()
    previous = deepcopy(ui.session_state['workflow-run-reveal:fixture'])
    ui.button(key='workflow-run-failure:fixture').click().run()
    current = deepcopy(ui.session_state['workflow-run-reveal:fixture'])
    assert current['serial'] > previous['serial']
    for node, serial, outcome in [('sft', previous['serial'], 'completed'),
                                  ('ingest', current['serial'], 'completed'),
                                  ('sft', current['serial'], 'pending')]:
        ui.session_state['fixture-canvas-event'] = {'action': 'reveal', 'node': node,
            'request_serial': serial, 'outcome': outcome, 'serial': 'stale-ack'}
        ui.run()
        assert not ui.exception
        assert ui.session_state['workflow-run-reveal:fixture'] == current
    ui.session_state['fixture-canvas-event'] = {'action': 'reveal', 'node': 'sft',
        'request_serial': current['serial'], 'outcome': 'cancelled', 'serial': 'cancel-ack'}
    ui.run()
    assert not ui.exception
    assert not _navigation_receipts(ui)
    assert 'workflow-run-reveal:fixture' not in ui.session_state
    ui.run()
    assert 'reveal' not in ui.session_state['fixture-canvas-spec']
    assert ui.session_state['workflow-stage:fixture'] == 'sft'
    assert 'fixture-begin-calls' not in ui.session_state


def test_width_close_and_prompt_inspection_only_change_view_state():
    ui = AppTest.from_string(SCRIPT).run()
    ui.button(key='workflow-run-failure:fixture').click().run()
    before = {key: deepcopy(ui.session_state[key]) for key in
              ['fixture-state', 'fixture-recipe', 'workflow-form-draft:fixture']}
    view_state = {'id': 'saved-request', 'follow': False, 'serial': 'pinned'}
    cursor = {'id': 'saved-request', 'offset': 200}
    offer = {'id': 'saved-request', 'offset': 200, 'next_offset': 220}
    # Opening a different request is owned by the stream component; presentation
    # changes must not discard the reader's already acknowledged prefix.
    for key, value in [('workflow-token-view:fixture:sft', view_state),
                       ('workflow-token-cursor:fixture:sft', cursor)]:
        ui.session_state[key] = deepcopy(value)
    ui.button(key='workflow-wide-output:fixture').click().run()
    assert not ui.exception
    assert ui.session_state['fixture-canvas-spec']['inspector']['wide'] is True
    ui.selectbox(key='workflow-run-prompt-selection:fixture:sft').select('workflow.jev_score').run()
    assert not ui.exception
    assert ui.text_area(key='workflow-run-prompt-body:fixture:sft:workflow.jev_score').disabled
    assert ui.text_area(key='workflow-run-prompt-body:fixture:sft:workflow.jev_score').value == 'Pinned quality instructions.'
    ui.session_state['workflow-token-offer:fixture:sft'] = deepcopy(offer)
    ui.button(key='close-node-output:fixture').click().run()
    assert not ui.exception
    assert ui.session_state['fixture-canvas-spec']['inspector']['open'] is False
    assert 'workflow-run-reveal:fixture' not in ui.session_state
    assert ui.session_state['workflow-stage:fixture'] == 'sft'
    assert ui.session_state['workflow-token-view:fixture:sft'] == view_state
    assert ui.session_state['workflow-token-cursor:fixture:sft'] == cursor
    assert ui.session_state['workflow-token-offer:fixture:sft'] == offer
    for key, value in before.items():
        assert ui.session_state[key] == value
    assert 'fixture-begin-calls' not in ui.session_state
