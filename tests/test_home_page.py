"""Home shortcuts and summaries use application data without filesystem reads."""
from pathlib import Path
import ast
import re

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
    assert ui.button(key='overview-target:orpo')


def test_home_exposes_every_target_and_shortcuts_do_not_replace_other_draft_fields():
    from lib.domain.workflow_targets import TARGETS
    ui=AppTest.from_string(SCRIPT).run()
    draft = {'workflow-name:fixture': 'Current independent work',
             'workflow-count:fixture': 50000}
    ui.session_state['workflow-form-draft:fixture'] = draft
    ui.session_state['workflow-entry-target:other'] = 'cot'
    keys = {button.key for button in ui.button}
    assert {f'overview-target:{target}' for target in TARGETS} <= keys
    for target in TARGETS:
        assert not re.search(r'[\u4e00-\u9fff]', ui.button(key=f'overview-target:{target}').label)
        ui.button(key=f'overview-target:{target}').click().run()
        assert not ui.exception
        assert ui.session_state['workflow-entry-target:fixture'] == target
        assert ui.session_state['nav'] == '自动工作流'
        assert ui.session_state['workflow-form-draft:fixture'] == draft
        assert ui.session_state['workflow-entry-target:other'] == 'cot'


def test_home_agent_and_recent_task_shortcuts_keep_context():
    ui=AppTest.from_string(SCRIPT).run()
    ui.button(key='overview:Agent 上下文').click().run()
    assert ui.session_state['workflow-source-mode:fixture']=='Agent 上下文'
    assert ui.session_state['workflow-preset:fixture']=='Agent 轨迹'
    ui.button(key='overview-run:0').click().run()
    assert ui.session_state['task-center-run:fixture']=='0'
    assert ui.session_state['nav']=='任务管理'


def test_home_document_and_manual_entries_keep_draft_and_workspace_context_without_format_duplicates():
    ui = AppTest.from_string(SCRIPT).run()
    draft = {'workflow-name:fixture': 'Independent work', 'workflow-count:fixture': 50000}
    ui.session_state['workflow-form-draft:fixture'] = draft
    ui.session_state['workflow-creation-mode:other'] = '人工制作图文'
    ui.session_state['workflow-upload-format:other'] = 'TXT'
    assert not any((button.key or '').startswith('overview-import:') for button in ui.button)
    ui.session_state['workflow-creation-mode:fixture'] = '人工制作图文'
    ui.button(key='overview:文档资料').click().run()
    assert not ui.exception
    assert ui.session_state['workflow-source-mode:fixture'] == '文档资料'
    assert ui.session_state['workflow-creation-mode:fixture'] == '自动生成'
    assert ui.session_state['nav'] == '自动工作流'
    assert ui.session_state['workflow-form-draft:fixture'] == draft
    ui.button(key='overview-manual').click().run()
    assert not ui.exception
    assert ui.session_state['workflow-creation-mode:fixture'] == '人工制作图文'
    assert ui.session_state['nav'] == '自动工作流'
    assert ui.session_state['workflow-form-draft:fixture'] == draft
    assert ui.session_state['workflow-creation-mode:other'] == '人工制作图文'
    assert ui.session_state['workflow-upload-format:other'] == 'TXT'
    ui.button(key='overview-target:cpt').click().run()
    assert ui.session_state['workflow-creation-mode:fixture'] == '自动生成'
