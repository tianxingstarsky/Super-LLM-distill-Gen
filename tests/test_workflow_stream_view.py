"""Node-open streaming, safe selections, and bounded incremental transport."""
import json

from streamlit.testing.v1 import AppTest

from lib.presentation.streamlit.workflow_stream_view import accept_receipt, accept_selection, stream_spec


SCRIPT = '''
import streamlit as st
from lib.presentation.streamlit.workflow_stream_view import render_stream_output
from lib.presentation.streamlit.i18n import install_streamlit_localization
install_streamlit_localization()
st.session_state.setdefault('canvas-open:live-canvas:fixture', True)
class Application:
    def read_streams(self, run_id, *, stage=None):
        st.session_state['reads'] = st.session_state.get('reads', 0) + 1
        if st.session_state.get('read-failed'):
            raise ValueError('untrusted raw provider error')
        st.session_state['read-scope'] = (run_id, stage)
        return st.session_state.get('rows', [])
    def read_stream_delta(self, run_id, request_id, *, stage=None, offset=0):
        st.session_state['delta-scope'] = (run_id, request_id, stage, offset)
        return {'id': request_id, 'stage': stage, 'status':'active', 'offset':offset,
                'next_offset':offset+10, 'events':[{'channel':'text','text':'<script>literal</script>'}],
                'done':False, 'truncated':False, 'legacy':False, 'text':'', 'reasoning':''}
from unittest.mock import patch
from lib.presentation.streamlit import workflow_stream_view
original_output = workflow_stream_view._output
def output(**kwargs):
    value = original_output(**kwargs)
    return st.session_state.get('component-event', value)
with patch.object(workflow_stream_view, '_output', side_effect=output):
    render_stream_output(Application(), 'fixture', 'sft')
'''


def row(request_id, **values):
    return dict(id=request_id, stage='sft', unit='unit-1', role='generation', status='active',
                text='A stored preview tail', reasoning='', updated_at='2026-10-07T08:30:00+00:00',
                started_at='2026-10-07T08:29:00+00:00', **values)


def component_spec(ui):
    return json.loads(ui.get('component_instance')[0].proto.json_args)['spec']


def test_closed_node_never_reads_or_renders_model_output():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['canvas-open:live-canvas:fixture'] = False
    ui.session_state['rows'] = [row('active-1')]
    ui.run()
    assert not ui.exception and not ui.get('component_instance')
    assert 'reads' not in ui.session_state


def test_open_node_uses_real_incremental_data_and_filters_other_nodes():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['rows'] = [row('active-1'), dict(row('other'), stage='preference', text='OTHER NODE')]
    ui.run()
    assert not ui.exception
    spec = component_spec(ui)
    assert spec['context'] == 'fixture:sft'
    assert [item['id'] for item in spec['rows']] == ['active-1']
    assert spec['rows'][0]['text'] == ''  # Full snapshots are never broadcast on every tick.
    assert spec['delta']['events'][0]['text'] == '<script>literal</script>'
    assert ui.session_state['delta-scope'] == ('fixture', 'active-1', 'sft', 0)
    ui.run()
    assert ui.session_state['delta-scope'][-1] == 0
    assert component_spec(ui)['delta'] == spec['delta']  # Unconfirmed page is retransmitted intact.
    ui.session_state['component-event'] = {
        'context':'fixture:sft', 'request_id':'active-1', 'receipt':True,
        'selection_serial':None, 'next_offset':10, 'serial':'received-1'}
    ui.run()
    assert ui.session_state['workflow-token-cursor:fixture:sft']['offset'] == 10
    ui.run()
    assert ui.session_state['delta-scope'][-1] == 10
    assert component_spec(ui)['delta']['offset'] == 10


def test_selected_request_can_stay_fixed_while_other_request_streams():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['rows'] = [row('active-2'), dict(row('done-1'), status='completed')]
    ui.session_state['workflow-token-view:fixture:sft'] = {'follow':False, 'id':'done-1', 'serial':'picked'}
    ui.run()
    assert not ui.exception
    assert ui.session_state['delta-scope'][1] == 'done-1'
    assert component_spec(ui)['ack_serial'] == 'picked'
    ui.session_state['rows'] = [row('active-2')]
    ui.run()
    assert not ui.exception and component_spec(ui)['selected_id'] is None
    assert component_spec(ui)['rows'][0]['text'] == ''


def test_selection_of_same_request_starts_again_without_accepting_old_epoch_receipt():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['rows'] = [row('active-1')]
    ui.run()
    ui.session_state['component-event'] = {
        'context':'fixture:sft', 'request_id':'active-1', 'receipt':True,
        'selection_serial':None, 'next_offset':10, 'serial':'received-1'}
    ui.run()
    ui.session_state['component-event'] = {
        'context':'fixture:sft', 'request_id':'active-1', 'follow_latest':False, 'serial':'picked'}
    ui.run()
    assert 'workflow-token-cursor:fixture:sft' not in ui.session_state
    ui.session_state['component-event'] = {
        'context':'fixture:sft', 'request_id':'active-1', 'receipt':True,
        'selection_serial':None, 'next_offset':10, 'serial':'old-receipt'}
    ui.run()
    assert component_spec(ui)['delta']['offset'] == 0
    assert component_spec(ui)['ack_serial'] == 'picked'
    assert 'workflow-token-cursor:fixture:sft' not in ui.session_state


def test_receipt_belongs_to_exact_offer_request_node_and_selection_epoch():
    offer = {'id':'request', 'offset':10, 'next_offset':20}
    event = {'context':'run:sft', 'request_id':'request', 'receipt':True,
             'selection_serial':'selection', 'next_offset':20, 'serial':'received'}
    assert accept_receipt(event, 'run:sft', 'request', 'selection', offer) == 20
    for change in ({'context':'other:sft'}, {'request_id':'other'}, {'receipt':1},
                   {'selection_serial':'old'}, {'next_offset':True}, {'next_offset':19},
                   {'next_offset':21}, {'serial':''}, {'serial':[]}):
        assert accept_receipt({**event, **change}, 'run:sft', 'request', 'selection', offer) is None
    assert accept_receipt(event, 'run:sft', 'request', 'selection', None) is None


def test_stream_spec_preserves_literal_text_and_excludes_internal_fields():
    original = dict(row('request'), checkpoint='PRIVATE PATH', secret='PRIVATE KEY')
    original['text'] = '<img src=x onerror=alert(1)>'
    spec = stream_spec([original, dict(row('other'), stage='agent')], 'run-a', 'sft', 'en')
    assert spec['rows'][0]['text'] == original['text']
    assert len(spec['rows']) == 1 and spec['language'] == 'en'
    assert 'PRIVATE' not in json.dumps(spec)
    assert original['checkpoint'] == 'PRIVATE PATH'


def test_selection_requires_exact_node_and_known_request_and_unique_serial():
    valid = {'context':'run:sft', 'request_id':'request', 'follow_latest':False, 'serial':'one'}
    assert accept_selection(valid, 'run:sft', {'request'}, None)['id'] == 'request'
    assert accept_selection(valid, 'run:sft', {'request'}, 'one') is None
    for change in ({'context':'other:sft'}, {'request_id':'other'}, {'request_id':[]}, {'follow_latest':1},
                   {'serial':''}, {'serial':'x'*129}, {'reset':'yes'}):
        assert accept_selection({**valid, **change}, 'run:sft', {'request'}, None) is None
    latest = {**valid, 'request_id':None, 'follow_latest':True, 'reset':True}
    assert accept_selection(latest, 'run:sft', {'request'}, None)['reset'] is True


def test_read_failure_removes_stale_output_without_leaking_provider_errors():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['rows'] = [row('active-1')]
    ui.session_state['ui_language'] = 'en'
    ui.run()
    assert not ui.exception and component_spec(ui)['language'] == 'en'
    ui.session_state['read-failed'] = True
    ui.run()
    assert not ui.exception and not ui.get('component_instance')
    assert ui.caption[0].value.startswith('Live output is temporarily unavailable.')
    assert 'untrusted raw' not in ui.caption[0].value
