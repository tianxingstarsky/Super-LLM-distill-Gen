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

TOKEN_LIMITS = {'context_window_tokens': 131_072, 'max_output_tokens': 32_768}


def _field(ui, label):
    return next(field for field in ui.text_input if field.label == label)


def _submit(ui, *, name='writer', model='alpha'):
    _field(ui, '服务名称').set_value(name)
    _field(ui, '服务 API 地址').set_value('https://models.example.test/v1')
    _field(ui, '模型名称').set_value(model)
    next(item for item in ui.number_input if item.label == '输入单价（美元 / 百万 tokens）').set_value(0.25)
    next(item for item in ui.number_input if item.label == '输出单价（美元 / 百万 tokens）').set_value(1.0)
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
    assert saved['api_format'] == 'chat'
    assert saved['prices'] == {'input_per_1m_usd': 0.25, 'output_per_1m_usd': 1.0}
    assert saved['api_key_env'] == 'OPENAI_API_KEY'
    assert ui.session_state['workflow-node-bindings:demo']['sft'] == {
        'generation': {'backend': 'writer', 'model': 'alpha', **TOKEN_LIMITS},
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
        'generation': {'backend': 'writer', 'model': 'alpha', **TOKEN_LIMITS},
        'jev': {'backend': 'judge', 'model': 'judge-v1', **TOKEN_LIMITS},
    }


def test_invalid_connection_does_not_leak_secret_or_save_endpoint():
    ui = AppTest.from_string(SCRIPT).run()
    _field(ui, '服务名称').set_value('bad name')
    _field(ui, '服务 API 地址').set_value('https://models.example.test/v1')
    _field(ui, '模型名称').set_value('alpha')
    next(item for item in ui.number_input if item.label == '输入单价（美元 / 百万 tokens）').set_value(0.25)
    next(item for item in ui.number_input if item.label == '输出单价（美元 / 百万 tokens）').set_value(1.0)
    _field(ui, '环境变量名或密钥').set_value('PRIVATE_TEST_VALUE')
    next(button for button in ui.button if button.label == '保存并用于当前节点').click().run()
    assert not ui.exception
    assert 'fixture-local' not in ui.session_state
    assert 'PRIVATE_TEST_VALUE' not in '\n'.join(item.value for item in ui.error)


def test_missing_prices_cannot_save_a_silent_budget_bypass():
    ui = AppTest.from_string(SCRIPT).run()
    _field(ui, '服务名称').set_value('writer')
    _field(ui, '服务 API 地址').set_value('https://models.example.test/v1')
    _field(ui, '模型名称').set_value('alpha')
    next(button for button in ui.button if button.label == '保存并用于当前节点').click().run()
    assert not ui.exception
    assert any('请填写输入和输出单价' in item.value for item in ui.error)
    assert 'fixture-local' not in ui.session_state


def test_explicit_free_service_saves_both_zero_rates():
    ui = AppTest.from_string(SCRIPT).run()
    _field(ui, '服务名称').set_value('local')
    _field(ui, '服务 API 地址').set_value('http://127.0.0.1:11434/v1')
    _field(ui, '模型名称').set_value('local-model')
    next(item for item in ui.checkbox if item.label == '此服务明确无需按 token 计费').set_value(True)
    next(button for button in ui.button if button.label == '保存并用于当前节点').click().run()
    assert not ui.exception
    assert ui.session_state['fixture-local']['backends']['local']['prices'] == {
        'input_per_1m_usd': 0.0, 'output_per_1m_usd': 0.0}


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
        'generation': {'backend': 'writer', 'model': 'alpha', **TOKEN_LIMITS},
        'jev': {'backend': 'judge', 'model': 'judge-v1', **TOKEN_LIMITS},
    }}
    ui.session_state['workflow-node-bindings:demo'] = original
    ui.run()
    _field(ui, '服务名称').set_value('alternative')
    _field(ui, '服务 API 地址').set_value('https://models.example.test/v1')
    _field(ui, '模型名称').set_value('beta')
    next(item for item in ui.number_input if item.label == '输入单价（美元 / 百万 tokens）').set_value(0.25)
    next(item for item in ui.number_input if item.label == '输出单价（美元 / 百万 tokens）').set_value(1.0)
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
    assert any(field.label == 'Service API URL' for field in ui.text_input)
    assert any(button.label == 'Save and use on this node' for button in ui.button)
    assert any(item.label == 'Add or update a model connection' for item in ui.expander)


def test_inline_anthropic_connection_uses_its_own_protocol_and_default_key_name():
    ui = AppTest.from_string(SCRIPT).run()
    next(box for box in ui.selectbox if box.label == 'API 协议').set_value('anthropic')
    _submit(ui, name='claude', model='claude-test')
    saved = ui.session_state['fixture-local']['backends']['claude']
    assert saved['api_format'] == 'anthropic'
    assert saved['api_key_env'] == 'ANTHROPIC_API_KEY'
    assert saved['base_url'] == 'https://models.example.test/v1'
    assert 'api_key' not in saved


def test_node_token_limits_are_role_specific_and_saved_with_binding():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['fixture-local'] = {'backends': {
        'writer': {'base_url': 'https://models.example.test/v1', 'models': ['alpha'],
                   'api_key_env': 'WRITER_KEY', 'api_format': 'responses'},
        'judge': {'base_url': 'https://models.example.test/v1', 'models': ['judge-v1'],
                  'api_key_env': 'JUDGE_KEY', 'api_format': 'chat'},
    }}
    ui.session_state['workflow-node-bindings:demo'] = {'sft': {
        'generation': {'backend': 'writer', 'model': 'alpha'},
        'jev': {'backend': 'judge', 'model': 'judge-v1'},
    }}
    ui.run()
    assert not ui.exception
    assert ui.number_input(key='node-model:demo:sft:generation:context:writer:alpha').value == 131_072
    assert ui.number_input(key='node-model:demo:sft:generation:output:writer:alpha').value == 32_768
    ui.number_input(key='node-model:demo:sft:generation:context:writer:alpha').set_value(196_608).run()
    ui.number_input(key='node-model:demo:sft:generation:output:writer:alpha').set_value(65_536).run()
    ui.number_input(key='node-model:demo:sft:jev:output:judge:judge-v1').set_value(48_000).run()
    assert not ui.exception
    bindings = ui.session_state['workflow-node-bindings:demo']['sft']
    assert bindings['generation'] == {'backend': 'writer', 'model': 'alpha',
                                      'context_window_tokens': 196_608, 'max_output_tokens': 65_536}
    assert bindings['jev'] == {'backend': 'judge', 'model': 'judge-v1',
                               'context_window_tokens': 131_072, 'max_output_tokens': 48_000}


def test_node_rejects_output_limit_at_or_above_context_window():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['fixture-local'] = {'backends': {
        'writer': {'base_url': 'https://models.example.test/v1', 'models': ['alpha'],
                   'api_key_env': 'WRITER_KEY'},
    }}
    ui.session_state['workflow-node-bindings:demo'] = {'sft': {
        'generation': {'backend': 'writer', 'model': 'alpha'},
    }}
    ui.run()
    ui.number_input(key='node-model:demo:sft:generation:context:writer:alpha').set_value(16_000).run()
    assert not ui.exception
    assert any('单次输出上限必须小于上下文窗口' in item.value for item in ui.warning)
    assert 'generation' not in ui.session_state['workflow-node-bindings:demo']['sft']


def test_node_model_picker_accepts_explicit_custom_names():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['fixture-local'] = {'backends': {
        'writer': {'base_url': 'https://models.example.test/v1', 'models': ['alpha'],
                   'api_key_env': 'WRITER_KEY'},
    }}
    ui.session_state['workflow-node-bindings:demo'] = {'sft': {
        'generation': {'backend': 'writer', 'model': 'alpha'},
    }}
    ui.run()
    assert not ui.exception
    picker = ui.selectbox(key='node-model:demo:sft:generation:model:writer')
    assert picker.proto.accept_new_options
