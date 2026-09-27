"""Package selection reads compact inventory and only the selected state."""
from streamlit.testing.v1 import AppTest

SCRIPT = '''
import streamlit as st
from lib.presentation.streamlit.package_page import render_package_page
st.session_state.setdefault('ws','fixture')
class Application:
    def list_runs(self): raise AssertionError('full inventory must not be loaded')
    def task_runs(self):
        ids = ['b'] if st.session_state.get('removed') else ['a','b']
        return [dict(id=i,name='工作流 '+i,status='completed',targets=['rlaif'],created_at='2026-09-27') for i in ids]
    def list_releases(self): return []
    def bundle_job(self,run_id): return None
    def state(self,run_id):
        st.session_state['state_calls']=st.session_state.get('state_calls',[])+[run_id]
        return dict(targets=['rlaif'], stages={})
    def package_contents(self,run_id):
        return dict(manifest=dict(counts={'rlaif':1},sha256={},sources=[]),files=[],
                    quality=dict(targets={'rlaif':dict(total=1,eligible=1)}),bundle=None)
    def artifact_preview(self,*args,**kwargs): return []
render_package_page(Application())
'''


def test_package_only_loads_selected_state_and_recovers_missing_selection():
    ui=AppTest.from_string(SCRIPT.encode('ascii','backslashreplace').decode('ascii')).run()
    assert not ui.exception
    assert ui.session_state['state_calls']==['a']
    ui.selectbox(key='package-run:fixture').set_value('b').run()
    assert ui.session_state['state_calls']==['a','b']
    ui.selectbox(key='package-run:fixture').set_value('a').run()
    ui.session_state['removed']=True
    ui.run()
    assert not ui.exception
    assert ui.selectbox(key='package-run:fixture').value=='b'
    ui.button(key='package-review:b').click().run()
    assert ui.session_state['review-mode:fixture']=='RLAIF 反馈审核'
    assert ui.session_state['preference-review-run:rlaif']=='b'
    assert ui.session_state['nav']=='人工审核'
