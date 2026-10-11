"""Task-card timers update summaries without replaying the selected inspector."""
from streamlit.testing.v1 import AppTest


SCRIPT = '''
import streamlit as st
from unittest.mock import patch
from streamlit.runtime.scriptrunner_utils.script_run_context import ThreadState
from streamlit.runtime.scriptrunner import get_script_run_ctx
from streamlit.runtime.scriptrunner_utils.script_requests import RerunData
from lib.presentation.streamlit import task_management_page as page
st.session_state['fixture-full-renders']=st.session_state.get('fixture-full-renders',0)+1
st.session_state.setdefault('ws','fixture')
st.session_state.setdefault('nav','任务管理')
st.session_state.setdefault('task-view:fixture','数据工作流')
st.session_state.setdefault('task-center-search:fixture','Keep')
st.session_state.setdefault('task-center-run:fixture','two')
st.session_state.setdefault('workflow-stage:two','sft')
st.session_state.setdefault('canvas-open:live-canvas:two',True)
st.session_state.setdefault('fixture-job-status','running')
class Application:
    def task_runs(self):
        fragment_id=ThreadState.get().fragment_id
        reads=st.session_state.setdefault('fixture-inventory-reads',[])
        reads.append(fragment_id)
        return [{'id': identifier,'name':'Keep task '+identifier,
                 'status':st.session_state['fixture-job-status'],'targets':['sft'],
                 'stages':{'ingest':{'status':'completed'},'sft':{'status':'failed' if st.session_state['fixture-job-status']=='failed' else 'running'},'package':{'status':'pending'}}}
                for identifier in ('one','two')]
def detail(application,run_id,begin,**kwargs):
    st.session_state['fixture-detail-renders']=st.session_state.get('fixture-detail-renders',0)+1
    st.caption('DETAIL '+run_id)
    st.text_area('Draft prompt',value='My unfinished prompt',key='fixture-node-draft')
with patch.object(page,'render_run',detail):
    page.render_task_management(Application(),'fixture',lambda _:None,lambda:None)
if st.session_state.get('fixture-auto-refresh'):
    fragments=[item for item in st.session_state['fixture-inventory-reads'] if item]
    selected=fragments[0] if st.session_state.get('fixture-summary-refresh') else fragments[-1]
    st.session_state['fixture-job-status']='failed'
    if st.session_state.get('fixture-leave-route'):
        st.session_state['nav']='系统设置'
    if st.session_state.get('fixture-leave-view'):
        st.session_state['task-view:fixture']='人工增强工作'
    if st.session_state.get('fixture-leave-workspace'):
        st.session_state['ws']='other'
    get_script_run_ctx().script_requests.request_rerun(RerunData(
        fragment_id=selected,is_auto_rerun=True))
'''


def markup(ui, needle):
    return [item.proto.body for item in ui.get('html') if needle in item.proto.body]


def test_card_timer_refreshes_failed_status_without_rerunning_detail_or_resetting_inputs():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['fixture-auto-refresh'] = True
    ui.run()
    assert not ui.exception
    cards = markup(ui, 'class="df-task-card-top"')
    assert len(cards) == 2 and all('data-status="failed"' in card for card in cards)
    assert ui.session_state['fixture-full-renders'] == ui.session_state['fixture-detail-renders'] == 1
    assert ui.session_state['task-center-run:fixture'] == 'two'
    assert ui.session_state['workflow-stage:two'] == 'sft'
    assert ui.session_state['canvas-open:live-canvas:two'] is True
    assert ui.text_input(key='task-center-search:fixture').value == 'Keep'
    assert ui.text_area(key='fixture-node-draft').value == 'My unfinished prompt'
    assert ui.session_state['task-center-page:fixture'] == 0
    assert len(ui.session_state['fixture-inventory-reads']) == 4


def test_summary_timer_updates_attention_count_without_replaying_detail():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['fixture-auto-refresh'] = True
    ui.session_state['fixture-summary-refresh'] = True
    ui.run()
    assert not ui.exception
    summaries = markup(ui, 'class="df-task-summary"')
    assert len(summaries) == 1
    assert 'data-kind="attention"><span>待处理</span><strong>2</strong>' in summaries[0]
    assert 'data-kind="processing"><span>待启动 / 执行中</span><strong>0</strong>' in summaries[0]
    assert ui.session_state['fixture-full-renders'] == ui.session_state['fixture-detail-renders'] == 1


def test_card_click_updates_detail_once_and_keeps_node_draft():
    ui = AppTest.from_string(SCRIPT).run()
    assert not ui.exception and ui.session_state['task-center-run:fixture'] == 'two'
    ui.text_area(key='fixture-node-draft').set_value('User edited draft').run()
    before = ui.session_state['fixture-detail-renders']
    ui.button(key='task-card:fixture:one').click().run()
    assert not ui.exception
    assert ui.session_state['task-center-run:fixture'] == 'one'
    assert any(item.value == 'DETAIL one' for item in ui.caption)
    assert ui.text_area(key='fixture-node-draft').value == 'User edited draft'
    assert ui.session_state['fixture-detail-renders'] == before + 1


def test_queued_card_timer_stops_reads_after_route_view_or_workspace_exit():
    for key in ('fixture-leave-route', 'fixture-leave-view', 'fixture-leave-workspace'):
        ui = AppTest.from_string(SCRIPT)
        ui.session_state['fixture-auto-refresh'] = True
        ui.session_state[key] = True
        ui.run()
        assert not ui.exception
        assert len(ui.session_state['fixture-inventory-reads']) == 3
        assert ui.session_state['fixture-full-renders'] == ui.session_state['fixture-detail-renders'] == 1


def test_background_status_change_keeps_filtered_selected_task_visible_until_user_changes_filter():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['task-center-filter:fixture'] = '未结束'
    ui.session_state['fixture-auto-refresh'] = True
    ui.run()
    assert not ui.exception
    assert ui.segmented_control(key='task-center-filter:fixture').value == '未结束'
    assert ui.session_state['task-center-run:fixture'] == 'two'
    assert len([button for button in ui.button if button.key.startswith('task-card:fixture:')]) == 2
    assert all('data-status="failed"' in card for card in markup(ui, 'class="df-task-card-top"'))
