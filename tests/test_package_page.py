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


RELEASE_SCRIPT = '''
import streamlit as st
from lib.presentation.streamlit.package_page import render_package_page
st.session_state.setdefault('ws', 'fixture')
st.session_state.setdefault('download_reads', 0)
class Application:
    def task_runs(self): return []
    def list_releases(self):
        return [dict(id='export/one', name='one', kind='export',
                     verified=not st.session_state.get('unverified'), error='file_hash_mismatch',
                     path='C:/release', files=[dict(name='sft.jsonl',
                     bytes=51*1024*1024 if st.session_state.get('large') else 4,
                     sha256='a'*64, path='C:/release/sft.jsonl')])]
    def release_file(self, release_id, filename):
        st.session_state['download_reads'] = st.session_state.get('download_reads', 0) + 1
        raise AssertionError('download must be deferred until click')
render_package_page(Application())
'''


def test_release_download_does_not_read_file_during_page_render():
    ui = AppTest.from_string(RELEASE_SCRIPT).run()
    assert not ui.exception
    assert ui.session_state['download_reads'] == 0


def test_unverified_release_keeps_warning_and_local_evidence_without_download():
    ui = AppTest.from_string(RELEASE_SCRIPT)
    ui.session_state['unverified'] = True
    ui.run()
    assert not ui.exception
    assert not ui.download_button
    assert any('file_hash_mismatch' in warning.value for warning in ui.warning)
    details = next(item for item in ui.get('expander') if item.label == '本地位置与校验详情')
    assert details.proto.expanded
    assert 'C:/release' in [item.value for item in details.get('code')]
    assert ui.session_state['download_reads'] == 0


def test_large_release_opens_selected_file_path_without_browser_download_or_eager_read():
    ui = AppTest.from_string(RELEASE_SCRIPT)
    ui.session_state['large'] = True
    ui.run()
    assert not ui.exception
    assert ui.selectbox(key='package-release-file:fixture:export/one').value == 'sft.jsonl'
    assert not ui.download_button
    details = next(item for item in ui.get('expander') if item.label == '本地位置与校验详情')
    assert details.proto.expanded
    paths = [item.value for item in details.get('code')]
    assert 'C:/release/sft.jsonl' in paths and 'C:/release' in paths
    assert any('50 MiB' in info.value for info in ui.info)
    assert ui.session_state['download_reads'] == 0
