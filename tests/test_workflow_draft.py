"""Exercise workbench drafts without storage or paid model calls."""
from streamlit.testing.v1 import AppTest
import pytest
from lib.presentation.streamlit import workflow_page


@pytest.fixture(autouse=True)
def restore_canvas_renderer(monkeypatch):
    monkeypatch.setattr(workflow_page, "render_canvas", workflow_page.render_canvas)

SCRIPT = '''
import streamlit as st
from lib.presentation.streamlit import workflow_page as page
from lib.application.workflow_node_models_service import WorkflowNodeModelsApplication
st.session_state.setdefault('ws','fixture')
class Sources:
    def source_files(self,*args,**kwargs):
        return [] if st.session_state.get('fixture-remove-source') else [{'path':'fixture.txt','label':'Fixture source'}]
    def task_runs(self): return []
    def agent_replay_capabilities(self): return {}
class Inventory:
    def list_backends(self):
        return {'default_backend':'local','default_model':'writer','roles':{},
                'backends':[{'name':'local','models':['writer','judge']}]}
def select_node(key,node):
    st.session_state[key]=node
def canvas(spec,selection_key,**kwargs):
    for node in spec['nodes']:
        st.button(node['id'],key='fixture-node:'+node['id'],on_click=select_node,args=(selection_key,node['id']))
page.render_canvas=canvas
st.checkbox('Show workbench',value=True,key='fixture-show')
if st.session_state['fixture-show']:
    page.render_workbench(Sources(),lambda args:None,WorkflowNodeModelsApplication(Inventory()))
'''

SCRIPT=SCRIPT.encode('ascii','backslashreplace').decode('ascii')

def test_large_run_inputs_survive_node_selection():
    ui=AppTest.from_string(SCRIPT).run()
    assert not ui.exception


def test_selected_goals_and_sources_survive_navigation_and_missing_file():
    ui = AppTest.from_string(SCRIPT).run()
    targets = 'workflow-targets:fixture:自动推荐'
    sources = 'workflow-sources:fixture:文档资料'
    ui.multiselect(key=targets).set_value(['orpo', 'rlaif']).run()
    ui.multiselect(key=sources).set_value(['fixture.txt']).run()
    ui.checkbox(key='fixture-show').uncheck().run()
    ui.checkbox(key='fixture-show').check().run()
    assert ui.multiselect(key=targets).value == ['orpo', 'rlaif']
    assert ui.multiselect(key=sources).value == ['fixture.txt']
    ui.segmented_control(key='workflow-preset:fixture').set_value('多轮对话').run()
    ui.segmented_control(key='workflow-preset:fixture').set_value('自动推荐').run()
    assert ui.multiselect(key=targets).value == ['orpo', 'rlaif']
    ui.session_state['fixture-remove-source'] = True
    ui.run()
    assert ui.multiselect(key=sources).value == []
    assert not ui.exception
    ui.number_input(key='workflow-count:fixture').set_value(50000).run()
    ui.number_input(key='workflow-batch-size:fixture').set_value(250).run()
    ui.number_input(key='workflow-concurrency:fixture').set_value(8).run()
    ui.text_input(key='workflow-name:fixture').set_value('Bulk run').run()
    ui.button(key='fixture-node:preference').click().run()
    assert not ui.exception
    assert ui.number_input(key='workflow-count:fixture').value==50000
    assert ui.number_input(key='workflow-batch-size:fixture').value==250
    assert ui.number_input(key='workflow-concurrency:fixture').value==8
    assert ui.text_input(key='workflow-name:fixture').value=='Bulk run'
    assert [m.value for m in ui.metric]==['200','8']


def test_conditional_settings_survive_goal_and_source_changes():
    ui=AppTest.from_string(SCRIPT).run()
    ui.segmented_control(key='workflow-preset:fixture').set_value('多轮对话').run()
    ui.number_input(key='workflow-turns:fixture').set_value(6).run()
    ui.number_input(key='workflow-chunk-chars:fixture').set_value(4800).run()
    ui.segmented_control(key='workflow-preset:fixture').set_value('ORPO 数据生成').run()
    ui.segmented_control(key='workflow-source-mode:fixture').set_value('开放需求').run()
    ui.segmented_control(key='workflow-preset:fixture').set_value('多轮对话').run()
    ui.segmented_control(key='workflow-source-mode:fixture').set_value('文档资料').run()
    assert not ui.exception
    assert ui.number_input(key='workflow-turns:fixture').value==6
    assert ui.number_input(key='workflow-chunk-chars:fixture').value==4800


def test_draft_survives_leaving_page_and_stays_workspace_scoped():
    ui=AppTest.from_string(SCRIPT).run()
    ui.number_input(key='workflow-count:fixture').set_value(50000).run()
    ui.text_input(key='workflow-name:fixture').set_value('Bulk run').run()
    ui.checkbox(key='fixture-show').uncheck().run()
    ui.checkbox(key='fixture-show').check().run()
    assert not ui.exception
    assert ui.number_input(key='workflow-count:fixture').value==50000
    assert ui.text_input(key='workflow-name:fixture').value=='Bulk run'
    ui.session_state['ws']='other'
    ui.run()
    assert ui.number_input(key='workflow-count:other').value==1000
    ui.session_state['ws']='fixture'
    ui.run()
    assert ui.number_input(key='workflow-count:fixture').value==50000
    assert ui.text_input(key='workflow-name:fixture').value=='Bulk run'


def test_clearing_numeric_input_restores_last_valid_value():
    ui=AppTest.from_string(SCRIPT).run()
    ui.number_input(key='workflow-count:fixture').set_value(50000).run()
    ui.number_input(key='workflow-count:fixture').set_value(None).run()
    assert not ui.exception
    assert ui.number_input(key='workflow-count:fixture').value==50000


def test_briefs_survive_source_changes_navigation_and_workspace_switch():
    ui = AppTest.from_string(SCRIPT).run()
    source = 'workflow-source-mode:fixture'
    ui.text_area(key='workflow-source-brief:fixture:文档资料').set_value('Document requirements').run()
    ui.segmented_control(key=source).set_value('开放需求').run()
    ui.text_area(key='workflow-open-brief:fixture').set_value('Generate 50K maintenance tasks').run()
    ui.segmented_control(key=source).set_value('Agent 上下文').run()
    ui.text_area(key='workflow-source-brief:fixture:Agent 上下文').set_value('Preserve tool evidence').run()
    ui.segmented_control(key=source).set_value('开放需求').run()
    assert ui.text_area(key='workflow-open-brief:fixture').value == 'Generate 50K maintenance tasks'
    ui.checkbox(key='fixture-show').uncheck().run()
    ui.checkbox(key='fixture-show').check().run()
    assert ui.text_area(key='workflow-open-brief:fixture').value == 'Generate 50K maintenance tasks'
    ui.session_state['ws'] = 'other'
    ui.run()
    assert ui.segmented_control(key='workflow-source-mode:other').value == '文档资料'
    ui.segmented_control(key='workflow-source-mode:other').set_value('开放需求').run()
    assert ui.text_area(key='workflow-open-brief:other').value == ''
    ui.session_state['ws'] = 'fixture'
    ui.run()
    ui.segmented_control(key=source).set_value('文档资料').run()
    assert ui.text_area(key='workflow-source-brief:fixture:文档资料').value == 'Document requirements'
    ui.segmented_control(key=source).set_value('Agent 上下文').run()
    assert ui.text_area(key='workflow-source-brief:fixture:Agent 上下文').value == 'Preserve tool evidence'
    ui.text_area(key='workflow-source-brief:fixture:Agent 上下文').set_value('').run()
    ui.segmented_control(key=source).set_value('文档资料').run()
    ui.segmented_control(key=source).set_value('Agent 上下文').run()
    assert ui.text_area(key='workflow-source-brief:fixture:Agent 上下文').value == ''
    assert not ui.exception


def test_quick_size_persists_and_open_brief_batch_count_respects_limit():
    ui = AppTest.from_string(SCRIPT).run()
    ui.button(key='workflow-count-preset:fixture:50000').click().run()
    assert ui.number_input(key='workflow-count:fixture').value == 50000
    ui.segmented_control(key='workflow-source-mode:fixture').set_value('开放需求').run()
    ui.number_input(key='workflow-max-units:fixture').set_value(1200).run()
    assert [m.value for m in ui.metric] == ['12', '4']
    assert any('处理上限低于候选规模' in item.value for item in ui.warning)
    ui.checkbox(key='fixture-show').uncheck().run()
    ui.checkbox(key='fixture-show').check().run()
    assert ui.number_input(key='workflow-count:fixture').value == 50000
    ui.button(key='workflow-count-preset:fixture:1000').click().run()
    assert ui.number_input(key='workflow-count:fixture').value == 1000
    assert [m.value for m in ui.metric] == ['10', '4']
    assert not ui.exception
