"""Exercise workbench drafts without storage or paid model calls."""
from streamlit.testing.v1 import AppTest

SCRIPT = '''
import streamlit as st
from unittest.mock import patch
from lib.presentation.streamlit import workflow_page as page
from lib.application.workflow_node_models_service import WorkflowNodeModelsApplication
st.session_state.setdefault('ws','fixture')
class Sources:
    def default_sft_output_style(self): return 'separated'
    def source_files(self,*args,**kwargs):
        return [] if st.session_state.get('fixture-remove-source') else [{'path':'fixture.txt','label':'Fixture source'}]
    def task_runs(self): return []
    def agent_replay_capabilities(self): return {}
    def web_research_capabilities(self):
        import os
        return {'brave_configured': bool(os.environ.get('DATAFORGE_BRAVE_SEARCH_API_KEY'))}
class Inventory:
    def list_backends(self):
        return {'default_backend':'local','default_model':'writer','roles':{},
                'backends':[{'name':'local','models':['writer','judge']}]}
def select_node(key,node):
    st.session_state[key]=node
def canvas(spec,selection_key,**kwargs):
    for node in spec['nodes']:
        st.button(node['id'],key='fixture-node:'+node['id'],on_click=select_node,args=(selection_key,node['id']))
st.checkbox('Show workbench',value=True,key='fixture-show')
if st.session_state['fixture-show']:
    with patch.object(page,'render_canvas',canvas):
        page.render_workbench(Sources(),lambda args:None,WorkflowNodeModelsApplication(Inventory()))
'''

SCRIPT=SCRIPT.encode('ascii','backslashreplace').decode('ascii')

def test_large_run_inputs_survive_node_selection():
    ui=AppTest.from_string(SCRIPT).run()
    assert not ui.exception


def test_sft_output_style_is_configured_in_node_and_kept_for_this_workspace():
    ui = AppTest.from_string(SCRIPT).run()
    key = 'workflow-sft-output-style:fixture'
    assert ui.selectbox(key=key).value == 'separated'
    ui.selectbox(key=key).set_value('drop').run()
    ui.button(key='fixture-node:ingest').click().run()
    assert ui.session_state['workflow-sft-output-style-draft:fixture'] == 'drop'
    ui.button(key='fixture-node:sft').click().run()
    assert ui.selectbox(key=key).value == 'drop'
    assert any('只保留答案' in item.proto.body for item in ui.get('html'))
    ui.session_state['ws'] = 'other'
    ui.run()
    assert ui.selectbox(key='workflow-sft-output-style:other').value == 'separated'


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


def test_public_web_search_requires_explicit_query_and_configured_key(monkeypatch):
    monkeypatch.delenv('DATAFORGE_BRAVE_SEARCH_API_KEY', raising=False)
    ui = AppTest.from_string(SCRIPT).run()
    assert not any(widget.key == 'workflow-web-research-enabled:fixture' for widget in ui.checkbox)
    ui.segmented_control(key='workflow-source-mode:fixture').set_value('开放需求').run()
    enabled = 'workflow-web-research-enabled:fixture'
    ui.checkbox(key=enabled).check().run()
    assert ui.text_input(key='workflow-web-research-query:fixture').value == ''
    assert ui.button(key='workflow-create:fixture').disabled
    ui.text_input(key='workflow-web-research-query:fixture').set_value('设备维护安全规范').run()
    ui.number_input(key='workflow-web-research-count:fixture').set_value(5).run()
    assert any('检索服务尚未配置' in warning.value for warning in ui.warning)
    monkeypatch.setenv('DATAFORGE_BRAVE_SEARCH_API_KEY', 'fixture-only')
    ui.run()
    assert not any('检索服务尚未配置' in warning.value for warning in ui.warning)
    assert ui.checkbox(key=enabled).value is True
    assert ui.text_input(key='workflow-web-research-query:fixture').value == '设备维护安全规范'
    assert ui.number_input(key='workflow-web-research-count:fixture').value == 5
    assert not ui.exception


def test_public_search_topics_are_optional_but_bounded(monkeypatch):
    monkeypatch.setenv('DATAFORGE_BRAVE_SEARCH_API_KEY', 'fixture-only')
    ui = AppTest.from_string(SCRIPT).run()
    ui.segmented_control(key='workflow-source-mode:fixture').set_value('开放需求').run()
    ui.text_area(key='workflow-open-brief:fixture').set_value('Generate maintenance exercises').run()
    ui.checkbox(key='workflow-web-research-enabled:fixture').check().run()
    ui.text_input(key='workflow-web-research-query:fixture').set_value('设备维护安全规范').run()
    extra = 'workflow-web-research-more:fixture'
    ui.text_area(key=extra).set_value('设备检修风险\n维护记录质量规范').run()
    assert not any('补充公开主题最多' in warning.value for warning in ui.warning)
    assert ui.text_area(key=extra).value == '设备检修风险\n维护记录质量规范'
    ui.text_area(key=extra).set_value('主题一\n主题二\n主题三\n主题四\n主题五').run()
    assert any('补充公开主题最多' in warning.value for warning in ui.warning)
    assert ui.button(key='workflow-create:fixture').disabled
    assert not ui.exception


def test_web_search_explains_private_query_and_agent_only_goal(monkeypatch):
    monkeypatch.setenv('DATAFORGE_BRAVE_SEARCH_API_KEY', 'fixture-only')
    ui = AppTest.from_string(SCRIPT).run()
    ui.segmented_control(key='workflow-source-mode:fixture').set_value('开放需求').run()
    ui.text_area(key='workflow-open-brief:fixture').set_value('生成设备维护训练题').run()
    ui.checkbox(key='workflow-web-research-enabled:fixture').check().run()
    ui.text_input(key='workflow-web-research-query:fixture').set_value('person@example.org').run()
    assert any('检索词疑似包含私有信息' in warning.value for warning in ui.warning)
    assert ui.button(key='workflow-create:fixture').disabled
    ui.text_input(key='workflow-web-research-query:fixture').set_value('设备维护安全规范').run()
    ui.multiselect(key='workflow-targets:fixture:自动推荐').set_value(['agent']).run()
    assert any('联网检索只为开放任务规划提供线索' in warning.value for warning in ui.warning)
    assert ui.button(key='workflow-create:fixture').disabled
    assert not ui.exception
