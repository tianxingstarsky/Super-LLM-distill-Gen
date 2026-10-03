"""A model service can be connected without leaving the selected workflow node."""
from streamlit.testing.v1 import AppTest


SCRIPT = '''
import streamlit as st
from lib.application.backend_service import BackendApplication
from lib.presentation.streamlit.workflow_node_settings import render_node_models

class Port:
    def read_config(self, filename='backends.yaml'):
        if filename == 'backends.local.yaml':
            return st.session_state.get('fixture-local', {})
        return {'backends': {}, 'default_backend': ''}
    def env_present(self, name):
        return False
    def spent_usd(self):
        return 0
    def write_local(self, value):
        st.session_state['fixture-local'] = value

application = BackendApplication(Port())
endpoints = {row['name']: row for row in application.list_backends()['backends']}
bindings = st.session_state.get('workflow-node-bindings:demo', {})
render_node_models('sft', '文档资料', 'demo', bindings, endpoints,
                   backend_application=application)
'''


def _field(ui, label):
    return next(field for field in ui.text_input if field.label == label)


def _submit(ui, *, name='writer', model='alpha'):
    _field(ui, '服务名称').set_value(name)
    _field(ui, '兼容服务地址').set_value('https://models.example.test/v1')
    _field(ui, '模型名称').set_value(model)
    next(button for button in ui.button if button.label == '保存并用于当前节点').click().run()
    assert not ui.exception
    return ui


def test_empty_node_connects_service_and_binds_generation_in_place():
    ui = AppTest.from_string(SCRIPT).run()
    assert not ui.exception
    assert any('当前没有可用的模型连接' in item.value for item in ui.info)
    _submit(ui)
    saved = ui.session_state['fixture-local']['backends']['writer']
    assert saved['models'] == ['alpha']
    assert saved['api_key_env'] == 'OPENAI_API_KEY'
    assert ui.session_state['workflow-node-bindings:demo']['sft'] == {
        'generation': {'backend': 'writer', 'model': 'alpha'},
    }
    assert _field(ui, '服务名称').value == ''
    assert any(box.value == 'writer' for box in ui.selectbox)


def test_new_service_fills_missing_reviewer_without_replacing_writer():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['fixture-local'] = {
        'backends': {'writer': {'base_url': 'https://models.example.test/v1',
                                'models': ['alpha'], 'api_key_env': 'WRITER_KEY'}}
    }
    ui.session_state['workflow-node-bindings:demo'] = {
        'sft': {'generation': {'backend': 'writer', 'model': 'alpha'}}
    }
    ui.run()
    assert not ui.exception
    _submit(ui, name='judge', model='judge-v1')
    assert ui.session_state['workflow-node-bindings:demo']['sft'] == {
        'generation': {'backend': 'writer', 'model': 'alpha'},
        'jev': {'backend': 'judge', 'model': 'judge-v1'},
    }


def test_invalid_connection_does_not_leak_secret_or_save_endpoint():
    ui = AppTest.from_string(SCRIPT).run()
    _field(ui, '服务名称').set_value('bad name')
    _field(ui, '兼容服务地址').set_value('https://models.example.test/v1')
    _field(ui, '模型名称').set_value('alpha')
    _field(ui, '环境变量名或密钥').set_value('PRIVATE_TEST_VALUE')
    next(button for button in ui.button if button.label == '保存并用于当前节点').click().run()
    assert not ui.exception
    assert 'fixture-local' not in ui.session_state
    assert 'PRIVATE_TEST_VALUE' not in '\n'.join(item.value for item in ui.error)


def test_saved_key_is_not_left_in_the_node_form():
    ui = AppTest.from_string(SCRIPT).run()
    next(item for item in ui.radio if item.label == '凭据来源').set_value('直接填写密钥')
    _field(ui, '环境变量名或密钥').set_value('PRIVATE_TEST_VALUE')
    _submit(ui)
    saved = ui.session_state['fixture-local']['backends']['writer']
    assert saved['api_key'] == 'PRIVATE_TEST_VALUE'
    assert 'node-service-connect:demo:sft:secret:0' not in ui.session_state
    assert _field(ui, '环境变量名或密钥').value == ''


def test_new_connection_does_not_replace_models_already_selected_on_both_roles():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['fixture-local'] = {
        'backends': {
            'writer': {'base_url': 'https://models.example.test/v1', 'models': ['alpha'],
                       'api_key_env': 'WRITER_KEY'},
            'judge': {'base_url': 'https://models.example.test/v1', 'models': ['judge-v1'],
                      'api_key_env': 'JUDGE_KEY'},
        }
    }
    original = {'sft': {
        'generation': {'backend': 'writer', 'model': 'alpha'},
        'jev': {'backend': 'judge', 'model': 'judge-v1'},
    }}
    ui.session_state['workflow-node-bindings:demo'] = original
    ui.run()
    _field(ui, '服务名称').set_value('alternative')
    _field(ui, '兼容服务地址').set_value('https://models.example.test/v1')
    _field(ui, '模型名称').set_value('beta')
    next(button for button in ui.button if button.label == '保存连接').click().run()
    assert not ui.exception
    assert ui.session_state['workflow-node-bindings:demo'] == original
    assert 'alternative' in ui.session_state['fixture-local']['backends']


def test_inline_connection_controls_translate_to_english():
    script = SCRIPT.replace(
        'application = BackendApplication(Port())',
        "from lib.presentation.streamlit.i18n import install_streamlit_localization\n"
        "st.session_state['ui_language'] = 'en'\n"
        "install_streamlit_localization()\n"
        'application = BackendApplication(Port())',
    )
    ui = AppTest.from_string(script).run()
    assert not ui.exception
    assert any(field.label == 'Compatible service URL' for field in ui.text_input)
    assert any(button.label == 'Save and use on this node' for button in ui.button)
    assert any(item.label == 'Add or update a model connection' for item in ui.expander)
