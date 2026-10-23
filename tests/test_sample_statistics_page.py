from streamlit.testing.v1 import AppTest


SCRIPT = '''
import streamlit as st
from lib.domain.sample_distribution import Distribution
from lib.presentation.streamlit.sample_statistics_page import render_sample_statistics
st.session_state.setdefault('ws', 'fixture')
class Application:
    def task_runs(self):
        return [dict(id='a'*32,name='Real run',status='completed',targets=['sft'])]
    def sample_statistics(self, run_id):
        stats=Distribution(); stats.add('sft',dict(messages=[dict(role='user',content='Hello'),dict(role='assistant',content='Hi')]))
        result=dict(total=stats.result(),targets=dict(sft=stats.result()),manifest_digest='v1')
        return dict(manifest=dict(counts=dict(sft=1),sha256={'sft.jsonl':'a'*64},sources=[]),
                    files=[dict(name='sft.jsonl',bytes=1024,sha256='a'*64)],manifest_bytes=200,
                    statistics=result if not st.session_state.get('pending') else None,
                    job=st.session_state.get('job'),bundle=None)
    def start_sample_statistics(self, run_id):
        st.session_state['starts']=st.session_state.get('starts',0)+1
        st.session_state['job']=dict(status='queued')
        return st.session_state['job']
    def cancel_sample_statistics(self, run_id):
        st.session_state['job']=dict(status='cancelled')
render_sample_statistics(Application(), 'fixture')
'''


def test_cached_report_has_measured_sizes_no_estimated_zip_and_no_new_worker():
    ui = AppTest.from_string(SCRIPT).run()
    assert not ui.exception
    markup = '\n'.join(x.value for x in ui.get('html'))
    assert '1.00 KiB' in markup and '1.20 KiB' in markup
    assert '尚未生成' in markup
    assert 'starts' not in ui.session_state
    assert not ui.get('progress')
    ui.selectbox(key='sample-stat-scope:fixture:'+'a'*32+':v1').set_value('sft').run()
    assert not ui.exception
    assert ui.download_button


def test_pending_job_is_not_duplicated_and_can_be_cancelled():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['pending'] = True
    ui.run()
    assert not ui.exception and ui.session_state['starts'] == 1
    ui.run()
    assert not ui.exception and ui.session_state['starts'] == 1
    ui.button(key='sample-stat-cancel:fixture:'+'a'*32).click().run()
    assert ui.session_state['job']['status'] == 'cancelled'
    assert any(x.label == '重新统计' for x in ui.button)


def test_english_statistics_have_no_chinese_static_labels():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['ui_language'] = 'en'
    ui.run()
    assert not ui.exception
    markup = '\n'.join(x.value for x in ui.get('html'))
    assert 'Native records' in markup and 'Not built' in markup
    assert '原生样本' not in markup and '尚未生成' not in markup
