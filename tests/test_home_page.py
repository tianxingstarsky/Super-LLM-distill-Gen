"""Home shortcuts and summaries use application data without filesystem reads."""
from pathlib import Path
import ast

from streamlit.testing.v1 import AppTest


SCRIPT='''
import streamlit as st
from lib.presentation.streamlit.home_page import render_overview
from lib.presentation.streamlit.i18n import install_streamlit_localization
st.session_state['ui_language']='en'
install_streamlit_localization()
class Application:
    def task_runs(self):
        return [{'id':str(i),'name':'工作流','status':status,'targets':['orpo'],'updated_at':'2026-09-27'}
                for i,status in enumerate(['completed','failed','needs_attention','queued','running','cancelled'])]
    def list_runs(self):
        raise AssertionError('Home must use task summaries')
    def list_releases(self):
        return [{'verified':True},{'verified':False},{}]
def navigate(page):
    st.session_state['nav']=page
def open_folder():
    st.session_state['folder-requested']=True
render_overview(Application(),'fixture',[{'name':'工作流.txt','extension':'TXT','size':1024}],
                1,7,'source/工作流','output/工作流',navigate=navigate,open_folder=open_folder,job_status=lambda:None)
'''
SCRIPT=SCRIPT.encode('ascii','backslashreplace').decode('ascii')


def test_home_has_no_storage_or_bootstrap_dependencies():
    source=Path('lib/presentation/streamlit/home_page.py').read_text(encoding='utf-8')
    tree=ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node,ast.ImportFrom) and node.module:
            assert not node.module.startswith(('lib.infrastructure','lib.bootstrap','lib.workspace'))
    assert '.stat(' not in source and '.read_text(' not in source


def test_home_uses_real_summaries_and_preserves_user_names():
    ui=AppTest.from_string(SCRIPT).run()
    assert not ui.exception
    rendered=''.join(item.proto.body for item in ui.get('html'))
    assert '<small>Total workflows</small><strong>6</strong>' in rendered
    assert '<strong data-user-content>工作流</strong>' in rendered
    assert '工作流.txt' in rendered
    assert 'Choose ORPO' in [button.label.replace(' →','') for button in ui.button]


def test_home_orpo_shortcut_sets_only_its_workspace_recipe():
    from lib.presentation.streamlit.workflow_page import PRESETS
    ui=AppTest.from_string(SCRIPT).run()
    ui.button(key='overview-preset:ORPO 数据生成').click().run()
    assert not ui.exception
    assert ui.session_state['workflow-preset:fixture']=='ORPO 数据生成'
    assert ui.session_state['nav']=='自动工作流'
    assert PRESETS['ORPO 数据生成']==('orpo',)


def test_home_agent_and_recent_task_shortcuts_keep_context():
    ui=AppTest.from_string(SCRIPT).run()
    ui.button(key='overview:Agent 上下文').click().run()
    assert ui.session_state['workflow-source-mode:fixture']=='Agent 上下文'
    assert ui.session_state['workflow-preset:fixture']=='Agent 轨迹'
    ui.button(key='overview-run:0').click().run()
    assert ui.session_state['task-center-run:fixture']=='0'
    assert ui.session_state['nav']=='任务管理'
