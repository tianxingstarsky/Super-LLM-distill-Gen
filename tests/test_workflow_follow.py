"""Follow live stages while allowing deliberate inspection of earlier nodes."""
from copy import deepcopy

import pytest
from streamlit.testing.v1 import AppTest

SCRIPT = '''
from copy import deepcopy
from unittest.mock import patch
import streamlit as st
from lib.presentation.streamlit.workflow_page import render_run, GRAPH_LABELS
from lib.presentation.streamlit import workflow_canvas, workflow_stream_view
st.session_state['ws']='fixture'
st.session_state.setdefault('phase','ingest')
def inspect():
    serial=st.session_state.get('fixture-click-sequence',0)+1
    st.session_state['fixture-click-sequence']=serial
    st.session_state['fixture-event']={'node':'ingest','serial':serial}
st.button('Inspect input',on_click=inspect,key='inspect')
def canvas(**kwargs):
    st.session_state['fixture-selected']=kwargs['spec']['selected']
    st.session_state['fixture-canvas-spec']=deepcopy(kwargs['spec'])
    return st.session_state.get('fixture-event')
def output(**kwargs):
    st.session_state['fixture-stream-spec']=deepcopy(kwargs['spec'])
    st.session_state['fixture-stream-mounts']=st.session_state.get('fixture-stream-mounts',0)+1
    return None
class Application:
    def state(self,run_id):
        phase=st.session_state['phase']
        stages={key:{'label':label,'status':'pending','done':0,'total':50000}
                for key,label in GRAPH_LABELS.items()}
        stages['ingest']['status']='running' if phase=='ingest' else 'completed'
        if phase in ('sft','failed'):
            stages['sft']['status']='running' if phase=='sft' else 'failed'
        if phase=='completed':
            stages['sft']['status']=stages['package']['status']='completed'
        return {'name':'Offline bulk fixture','status':phase if phase in ('failed','completed') else 'running',
                'targets':['sft'],'stages':stages,'events':[]}
    def recipe(self,run_id): return {'targets':['sft'],'sources':[]}
    def is_active(self,run_id): return st.session_state['phase'] in ('ingest','sft')
    def read_streams(self,run_id,*,stage):
        st.session_state['fixture-stream-reads']=st.session_state.get('fixture-stream-reads',0)+1
        return [{'id':'request-'+stage,'stage':stage,'unit':'unit-1','role':'generation',
                 'status':'active','text':'','reasoning':'','started_at':'2026-10-09T10:00:00Z'}]
    def read_stream_delta(self,run_id,request_id,*,stage,offset=0):
        return {'id':request_id,'stage':stage,'status':'active','offset':offset,'next_offset':offset+10,
                'events':[{'channel':'text','text':'Received token'}],'done':False,'truncated':False}
with patch.object(workflow_canvas,'_canvas',canvas), patch.object(workflow_stream_view,'_output',output):
    render_run(Application(),'fixture',lambda args:None)
'''


def test_follow_advances_stages_and_manual_canvas_selection_pauses_it():
    ui = AppTest.from_string(SCRIPT).run()
    assert not ui.exception
    assert ui.session_state['fixture-selected']=='ingest'
    assert ui.session_state['fixture-canvas-spec']['expanded'] is True
    assert ui.session_state['fixture-canvas-spec']['inspector']['open'] is False
    assert 'fixture-stream-reads' not in ui.session_state
    ui.session_state['phase']='sft'
    ui.run()
    assert ui.session_state['fixture-selected']=='sft'
    ui.button(key='inspect').click().run()
    assert not ui.exception
    assert ui.session_state['fixture-selected']=='ingest'
    assert not ui.toggle(key='workflow-follow:fixture').value
    assert ui.session_state['fixture-canvas-spec']['inspector']['open'] is True
    assert ui.session_state['fixture-stream-spec']['context']=='fixture:ingest'
    assert ui.session_state['fixture-stream-spec']['reader_height']==220
    ui.toggle(key='workflow-follow:fixture').set_value(True).run()
    assert not ui.exception
    assert ui.session_state['fixture-selected']=='sft'
    assert ui.toggle(key='workflow-follow:fixture').value


def test_follow_locates_failure_and_packaging_after_completion():
    ui = AppTest.from_string(SCRIPT).run()
    ui.session_state['phase']='failed'
    ui.run()
    assert not ui.exception
    assert ui.session_state['fixture-selected']=='sft'
    ui.session_state['phase']='completed'
    ui.run()
    assert not ui.exception
    assert ui.session_state['fixture-selected']=='package'


def _preserved_reader_state(ui, stage):
    return {kind: deepcopy(ui.session_state[f'workflow-token-{kind}:fixture:{stage}'])
            for kind in ('view', 'cursor', 'offer')}


@pytest.mark.parametrize('follow', [False, True])
def test_wide_reading_and_native_close_preserve_follow_selection_and_stream_state(follow):
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['phase'] = 'sft'
    ui.run()
    ui.button(key='inspect').click().run()
    if follow:
        ui.toggle(key='workflow-follow:fixture').set_value(True).run()
    stage = 'sft' if follow else 'ingest'
    request = 'request-' + stage
    ui.session_state[f'workflow-token-view:fixture:{stage}'] = {
        'follow': False, 'id': request, 'serial': 'pinned-request'}
    ui.session_state[f'workflow-token-cursor:fixture:{stage}'] = {'id': request, 'offset': 20}
    ui.session_state[f'workflow-token-offer:fixture:{stage}'] = {
        'id': request, 'stage': stage, 'status': 'active', 'offset': 20, 'next_offset': 30,
        'events': [{'channel': 'text', 'text': 'Pending suffix'}], 'done': False, 'truncated': False}
    before = _preserved_reader_state(ui, stage)

    ui.button(key='workflow-wide-output:fixture').click().run()
    assert not ui.exception
    assert ui.session_state['canvas-wide:live-canvas:fixture'] is True
    assert ui.session_state['fixture-canvas-spec']['inspector']['wide'] is True
    assert ui.session_state['fixture-stream-spec']['reader_height'] == 380
    assert ui.session_state['fixture-stream-spec']['selected_id'] == request
    assert ui.session_state['fixture-selected'] == stage
    assert ui.toggle(key='workflow-follow:fixture').value is follow
    assert _preserved_reader_state(ui, stage) == before

    ui.button(key='workflow-wide-output:fixture').click().run()
    assert not ui.exception
    assert ui.session_state['canvas-wide:live-canvas:fixture'] is False
    assert ui.session_state['fixture-stream-spec']['reader_height'] == 220
    assert _preserved_reader_state(ui, stage) == before
    reads = ui.session_state['fixture-stream-reads']
    mounts = ui.session_state['fixture-stream-mounts']

    ui.button(key='close-node-output:fixture').click().run()
    assert not ui.exception
    assert ui.session_state['fixture-canvas-spec']['inspector']['open'] is False
    assert ui.session_state['workflow-stage:fixture'] == stage
    assert ui.toggle(key='workflow-follow:fixture').value is follow
    assert ui.session_state['fixture-stream-reads'] == reads
    assert ui.session_state['fixture-stream-mounts'] == mounts
    assert _preserved_reader_state(ui, stage) == before
    ui.run()  # The component still holds the original, already-consumed click.
    assert not ui.exception and ui.session_state['fixture-canvas-spec']['inspector']['open'] is False
    assert ui.session_state['fixture-stream-reads'] == reads
    assert _preserved_reader_state(ui, stage) == before

    ui.button(key='inspect').click().run()
    assert not ui.exception
    assert ui.session_state['fixture-canvas-spec']['inspector']['open'] is True
    assert ui.session_state['workflow-stage:fixture'] == 'ingest'
    assert not ui.toggle(key='workflow-follow:fixture').value
    assert _preserved_reader_state(ui, stage) == before


@pytest.mark.parametrize('follow', [False, True])
def test_runtime_bridge_close_does_not_change_follow_or_reopen_on_a_replayed_event(follow):
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['phase'] = 'sft'
    ui.run()
    ui.button(key='inspect').click().run()
    if follow:
        ui.toggle(key='workflow-follow:fixture').set_value(True).run()
    stage = 'sft' if follow else 'ingest'
    reads = ui.session_state['fixture-stream-reads']
    ui.session_state['fixture-event'] = {'action': 'close', 'node': stage, 'serial': 'escape-close'}
    ui.run()
    assert not ui.exception
    assert ui.session_state['canvas-open:live-canvas:fixture'] is False
    assert ui.session_state['fixture-canvas-spec']['inspector']['open'] is False
    assert ui.session_state['workflow-stage:fixture'] == stage
    assert ui.toggle(key='workflow-follow:fixture').value is follow
    assert ui.session_state['fixture-stream-reads'] == reads

    ui.session_state['fixture-event'] = {'node': stage, 'serial': 'escape-close'}
    ui.run()
    assert not ui.exception and ui.session_state['canvas-open:live-canvas:fixture'] is False
    assert ui.toggle(key='workflow-follow:fixture').value is follow
    assert ui.session_state['fixture-stream-reads'] == reads
    ui.button(key='inspect').click().run()
    assert not ui.exception and ui.session_state['canvas-open:live-canvas:fixture'] is True
    assert not ui.toggle(key='workflow-follow:fixture').value
