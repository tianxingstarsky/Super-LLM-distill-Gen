"""Follow live stages while allowing deliberate inspection of earlier nodes."""
from streamlit.testing.v1 import AppTest

SCRIPT = '''
import streamlit as st
from lib.presentation.streamlit.workflow_page import render_run, GRAPH_LABELS
from lib.presentation.streamlit import workflow_canvas
st.session_state['ws']='fixture'
st.session_state.setdefault('phase','ingest')
def inspect():
    st.session_state['fixture-event']={'node':'ingest','serial':1}
st.button('Inspect input',on_click=inspect,key='inspect')
def canvas(**kwargs):
    st.session_state['fixture-selected']=kwargs['spec']['selected']
    return st.session_state.get('fixture-event')
workflow_canvas._canvas=canvas
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
render_run(Application(),'fixture',lambda args:None)
'''


def test_follow_advances_stages_and_manual_canvas_selection_pauses_it():
    ui = AppTest.from_string(SCRIPT).run()
    assert not ui.exception
    assert ui.session_state['fixture-selected']=='ingest'
    ui.session_state['phase']='sft'
    ui.run()
    assert ui.session_state['fixture-selected']=='sft'
    ui.button(key='inspect').click().run()
    assert not ui.exception
    assert ui.session_state['fixture-selected']=='ingest'
    assert not ui.toggle(key='workflow-follow:fixture').value
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
