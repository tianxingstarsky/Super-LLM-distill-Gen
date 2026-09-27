"""Verify injected queue applications without filesystem composition."""
from streamlit.testing.v1 import AppTest

SCRIPT = '''
import streamlit as st
from lib.presentation.streamlit.review_overview import render_review_overview
class Queue:
    def __init__(self, count, field): self.count, self.field = count, field
    def reviewable_runs(self):
        if self.count is None: raise OSError('unavailable')
        return [{self.field:self.count}]
modes = ['SFT 数据调整','DPO 偏好优化','ORPO 偏好优化','RLAIF 反馈审核','CPT 语料审核']
apps = {mode: Queue(count,field) for mode,count,field in zip(modes,[50000,12,30,None,8],
        ['sample_count','pair_count','pair_count','pair_count','sample_count'])}
assert render_review_overview(apps, 'mode') is apps
st.write(st.session_state.get('mode',''))
'''


def test_queue_statistics_and_switch_use_injected_applications():
    ui = AppTest.from_string(SCRIPT.encode('ascii','backslashreplace').decode('ascii')).run()
    assert not ui.exception
    html = '\n'.join(item.proto.body for item in ui.get('html'))
    assert '<span>50000</span>' in html
    assert '<span>30</span>' in html
    assert '队列暂不可读取' in html
    ui.button(key='review-overview-open-orpo').click().run()
    assert ui.session_state['mode'] == 'ORPO 偏好优化'
    assert not ui.exception
