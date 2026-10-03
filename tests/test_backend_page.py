"""The backend overview must display real counts without leaking key fragments."""
from __future__ import annotations

import pytest

from lib.presentation.streamlit import backend_page
from streamlit.testing.v1 import AppTest


def test_backend_overview_uses_inventory_but_never_renders_masked_key(monkeypatch):
    fragments: list[str] = []
    monkeypatch.setattr(backend_page.st, "html", fragments.append)
    rows = [
        {"name": "<private>", "base_url": "http://127.0.0.1:11434/v1",
         "models": ["qwen:7b"], "roles": ["generation"], "is_default": True,
         "api_key": {"source": "configs/backends.local.yaml", "status": "sk-***3456"}},
        {"name": "empty", "base_url": "http://127.0.0.1:1234/v1",
         "models": [], "roles": [], "is_default": False,
         "api_key": {"source": "未配置", "status": "缺失"}},
    ]
    info = {"backends": rows, "roles": {"generation": {"backend": "<private>", "model": "qwen:7b"}},
            "spent": 1.25, "budget": {"max_total_usd": 5}}
    backend_page._summary(info)
    backend_page._endpoints(rows)
    markup = "\n".join(fragments)
    assert "已登记端点</small><strong>2" in markup
    assert "密钥已配置</small><strong>1" in markup
    assert "$1.25" in markup
    assert "密钥未配置" in markup
    assert "&lt;private&gt;" in markup
    assert "sk-" not in markup and "3456" not in markup and "<private>" not in markup


SCRIPT = '''
import streamlit as st
from lib.application.backend_service import BackendApplication
from lib.presentation.streamlit.backend_page import render_backend_page
from lib.llm_client import BudgetExceeded
from lib.presentation.streamlit.i18n import install_streamlit_localization
st.session_state['ui_language']='en'
install_streamlit_localization()
class Port:
    def read_config(self,filename):
        if filename=='backends.local.yaml':
            return st.session_state.get('fixture-local',{})
        return {'backends':{'工作流':{'base_url':'http://127.0.0.1:11434/v1',
                'models':['执行中'],'api_key_env':'FIXTURE_SECRET'}},
                'default_backend':'工作流','budget':{'max_total_usd':5}}
    def env_present(self,name): return False
    def spent_usd(self): return 1.25
    def write_local(self,value): st.session_state['fixture-local']=value
    def probe(self,name,backend):
        st.session_state['fixture-probed']=name
        if st.session_state.get('fixture-probe-fail'):
            raise RuntimeError('PRIVATE_TEST_ERROR')
        return {'models':['执行中']}
    def write_budget_reset(self,caller,limit):
        if st.session_state.get('fixture-budget-active'):
            raise BudgetExceeded('budget_reset_in_flight')
        st.session_state['fixture-budget-reset']=(caller,limit)
    def audit_budget_reset(self,caller,spent):
        st.session_state['fixture-budget-audit']=(caller,spent)
render_backend_page(BackendApplication(Port()))
'''
SCRIPT = SCRIPT.encode('ascii','backslashreplace').decode('ascii')


def test_service_page_uses_injected_application_and_preserves_user_names():
    ui = AppTest.from_string(SCRIPT).run()
    assert not ui.exception
    markup = ''.join(item.proto.body for item in ui.get('html'))
    assert '<strong data-user-content>工作流</strong>' in markup
    assert '<span data-user-content>执行中</span>' in markup
    assert 'Service addresses and credentials' in markup
    assert 'fixture-probed' not in ui.session_state
    next(button for button in ui.button if button.label=='Test connection').click().run()
    assert ui.session_state['fixture-probed']=='工作流'
    assert not ui.exception


def test_connection_errors_do_not_render_exception_details():
    ui = AppTest.from_string(SCRIPT).run()
    ui.session_state['fixture-probe-fail']=True
    next(button for button in ui.button if button.label=='Test connection').click().run()
    assert not ui.exception
    assert 'PRIVATE_TEST_ERROR' not in ''.join(item.value for item in ui.error)
    assert 'RuntimeError' in ui.error[0].value


def test_service_page_budget_reset_uses_current_application_budget():
    ui = AppTest.from_string(SCRIPT).run()
    ui.checkbox(key='budget-reset-confirm').check().run()
    # Simulate an external budget edit after the overview was rendered.
    ui.session_state['fixture-local']={'budget':{'max_total_usd':9}}
    next(button for button in ui.button if button.label=='Reset budget').click().run()
    assert not ui.exception
    assert ui.session_state['fixture-budget-reset']==('console',9.0)
    assert ui.session_state['fixture-budget-audit']==('console',1.25)


def test_budget_reset_explains_active_requests_without_claiming_success():
    ui = AppTest.from_string(SCRIPT).run()
    ui.session_state['fixture-budget-active'] = True
    ui.checkbox(key='budget-reset-confirm').check().run()
    next(button for button in ui.button if button.label == 'Reset budget').click().run()
    assert not ui.exception
    assert any('Model requests are still running. Reset the budget after they finish.' in item.value
               for item in ui.error)
    assert 'fixture-budget-reset' not in ui.session_state
    assert 'fixture-budget-audit' not in ui.session_state


def test_service_registration_saves_through_application_port():
    ui = AppTest.from_string(SCRIPT).run()
    next(button for button in ui.button if button.label=='Save endpoint').click().run()
    assert not ui.exception
    saved = ui.session_state['fixture-local']['backends']['local_gpu']
    assert saved['models']==['qwen2.5:7b-instruct']
    assert saved['api_format']=='chat'
    assert saved['api_key_env']=='OPENAI_API_KEY'
    assert 'api_key' not in saved


@pytest.mark.parametrize(('api_format', 'base_url', 'key_env', 'label'), [
    ('responses', 'https://api.openai.com/v1', 'OPENAI_API_KEY', 'OpenAI Responses'),
    ('anthropic', 'https://api.anthropic.com', 'ANTHROPIC_API_KEY', 'Anthropic Messages'),
])
def test_service_registration_saves_selected_api_format_and_shows_it(
        api_format, base_url, key_env, label):
    ui = AppTest.from_string(SCRIPT).run()
    ui.selectbox(key='backend-add-api-format').set_value(api_format).run()
    assert not ui.exception
    assert ui.text_input(key=f'backend-add-base-url:{api_format}').value == base_url
    ui.text_input(key=f'backend-add-models:{api_format}').set_value('chosen-model')
    next(button for button in ui.button if button.label == 'Save endpoint').click().run()
    assert not ui.exception
    saved = ui.session_state['fixture-local']['backends'][
        'openai_responses' if api_format == 'responses' else 'anthropic']
    assert saved['api_format'] == api_format
    assert saved['base_url'] == base_url
    assert saved['models'] == ['chosen-model']
    assert saved['api_key_env'] == key_env
    markup = ''.join(item.proto.body for item in ui.get('html'))
    assert label in markup
