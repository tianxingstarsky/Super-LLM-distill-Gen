"""A model service can be connected without leaving the selected workflow node."""
import pytest
from copy import deepcopy

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


def test_unconfigured_node_reuses_another_nodes_model_in_one_click_without_linking_changes():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['fixture-local'] = {'backends': {
        'writer': {'base_url': 'https://models.example.test/v1', 'models': ['alpha'],
                   'api_key_env': 'WRITER_KEY'},
    }}
    source = {'backend': 'writer', 'model': 'alpha',
              'context_window_tokens': 196_608, 'max_output_tokens': 65_536}
    ui.session_state['workflow-node-bindings:demo'] = {'cpt': {'generation': source}}
    ui.run()
    assert not ui.exception
    reuse = ui.button(key='node-model:demo:sft:generation:reuse:cpt')
    assert 'CPT' in reuse.label
    reuse.click().run()
    assert not ui.exception
    draft = ui.session_state['workflow-node-bindings:demo']
    assert draft['sft']['generation'] == source
    assert draft['cpt']['generation'] == source
    assert draft['sft']['generation'] is not draft['cpt']['generation']
    assert ui.number_input(key='node-model:demo:sft:generation:output:writer:alpha').value == 65_536
    assert not [button for button in ui.button if button.key == reuse.key]
    ui.number_input(key='node-model:demo:sft:generation:output:writer:alpha').set_value(48_000).run()
    assert ui.session_state['workflow-node-bindings:demo']['cpt']['generation']['max_output_tokens'] == 65_536


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


@pytest.mark.parametrize('existing_reviewer', [None, {
    'backend': 'judge', 'model': 'judge-v1', **TOKEN_LIMITS,
}])
def test_reviewer_explicitly_reuses_own_writer_and_keeps_limits_independent(existing_reviewer):
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['fixture-local'] = {'backends': {
        'writer': {'base_url': 'https://models.example.test/v1', 'models': ['alpha']},
        'judge': {'base_url': 'https://models.example.test/v1', 'models': ['judge-v1']},
    }}
    writer = {'backend': 'writer', 'model': 'alpha',
              'context_window_tokens': 196_608, 'max_output_tokens': 65_536}
    roles = {'generation': writer}
    if existing_reviewer:
        roles['jev'] = existing_reviewer
    ui.session_state['workflow-node-bindings:demo'] = {'sft': roles}
    ui.run()
    assert not ui.exception
    assert ui.session_state['workflow-node-bindings:demo']['sft'].get('jev') == existing_reviewer
    ui.button(key='node-model:demo:sft:jev:reuse-generation').click().run()
    assert not ui.exception
    saved = ui.session_state['workflow-form-draft:demo']['workflow-node-bindings:demo']
    assert saved['sft']['generation'] == saved['sft']['jev'] == writer
    assert saved['sft']['generation'] is not saved['sft']['jev']
    assert ui.selectbox(key='node-model:demo:sft:jev:backend').value == 'writer'
    assert ui.selectbox(key='node-model:demo:sft:jev:model:writer').value == 'alpha'
    assert not [button for button in ui.button if button.key == 'node-model:demo:sft:jev:reuse-generation']
    ui.number_input(key='node-model:demo:sft:jev:output:writer:alpha').set_value(48_000).run()
    assert not ui.exception
    saved = ui.session_state['workflow-form-draft:demo']['workflow-node-bindings:demo']
    assert saved['sft']['generation']['max_output_tokens'] == 65_536
    assert saved['sft']['jev']['max_output_tokens'] == 48_000
    ui.run()
    assert ui.number_input(key='node-model:demo:sft:jev:output:writer:alpha').value == 48_000


def test_configured_node_can_choose_existing_model_without_overwriting_until_apply():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['fixture-local'] = {'backends': {
        'writer': {'base_url': 'https://models.example.test/v1', 'models': ['alpha', 'beta']},
    }}
    original = {'backend': 'writer', 'model': 'beta', **TOKEN_LIMITS}
    source = {'backend': 'writer', 'model': 'alpha',
              'context_window_tokens': 196_608, 'max_output_tokens': 65_536}
    ui.session_state['workflow-node-bindings:demo'] = {
        'sft': {'generation': original}, 'cpt': {'generation': source},
        'multiturn': {'generation': {'backend': 'writer', 'model': 'alpha', **TOKEN_LIMITS}},
    }
    ui.run()
    assert not ui.exception
    ui.selectbox(key='node-model:demo:sft:generation:reuse-source').set_value('cpt').run()
    assert ui.session_state['workflow-node-bindings:demo']['sft']['generation'] == original
    ui.button(key='node-model:demo:sft:generation:reuse-apply').click().run()
    assert not ui.exception
    saved = ui.session_state['workflow-form-draft:demo']['workflow-node-bindings:demo']
    assert saved['sft']['generation'] == saved['cpt']['generation'] == source
    ui.number_input(key='node-model:demo:sft:generation:output:writer:alpha').set_value(40_000).run()
    assert ui.session_state['workflow-node-bindings:demo']['cpt']['generation']['max_output_tokens'] == 65_536


def test_review_picker_does_not_filter_out_the_generation_model():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['fixture-local'] = {'backends': {
        'writer': {'base_url': 'https://models.example.test/v1', 'models': ['alpha']},
    }}
    writer = {'backend': 'writer', 'model': 'alpha', **TOKEN_LIMITS}
    ui.session_state['workflow-node-bindings:demo'] = {'sft': {'generation': writer}}
    ui.run()
    ui.selectbox(key='node-model:demo:sft:jev:backend').set_value('writer').run()
    ui.selectbox(key='node-model:demo:sft:jev:model:writer').set_value('alpha').run()
    assert not ui.exception
    assert ui.session_state['workflow-node-bindings:demo']['sft']['jev'] == writer


def _confirmation_ui(*, form=None, roles=None):
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['fixture-local'] = {'backends': {
        'writer': {'base_url': 'https://models.example.test/v1', 'models': ['alpha', 'beta']},
    }}
    binding = {'backend': 'writer', 'model': 'alpha', **TOKEN_LIMITS}
    ui.session_state['workflow-node-bindings:demo'] = {
        'sft': roles if roles is not None else {'generation': dict(binding), 'jev': dict(binding)},
    }
    if form is not None:
        ui.session_state['workflow-form-draft:demo'] = form
    return ui.run()


def test_model_confirmation_is_one_explicit_node_action_and_invalidates_on_edits():
    ui = _confirmation_ui()
    assert not ui.exception
    assert ui.session_state['workflow-node-model-confirmations:demo'] == {}
    assert not ui.button(key='node-model-confirm:demo:sft').disabled
    assert sum(button.label == '确认模型配置' for button in ui.button) == 1
    ui.button(key='node-model-confirm:demo:sft').click().run()
    assert not ui.exception
    confirmed = ui.session_state['workflow-node-model-confirmations:demo']['sft']
    assert len(confirmed) == 64
    assert ui.button(key='node-model-confirm:demo:sft').disabled
    assert any(item.value == '已确认 · 配置已保存' for item in ui.caption)
    assert ui.session_state['workflow-form-draft:demo']['workflow-node-model-confirmations:demo']['sft'] == confirmed
    ui.number_input(key='node-model:demo:sft:generation:output:writer:alpha').set_value(40_000).run()
    assert not ui.exception
    assert 'sft' not in ui.session_state['workflow-node-model-confirmations:demo']
    assert not ui.button(key='node-model-confirm:demo:sft').disabled
    ui.number_input(key='node-model:demo:sft:generation:output:writer:alpha').set_value(32_768).run()
    assert 'sft' not in ui.session_state['workflow-node-model-confirmations:demo']
    ui.button(key='node-model-confirm:demo:sft').click().run()
    ui.selectbox(key='node-model:demo:sft:generation:model:writer').set_value('beta').run()
    assert not ui.exception
    assert 'sft' not in ui.session_state['workflow-node-model-confirmations:demo']


def test_confirmation_survives_restoring_a_saved_draft_but_pending_draft_stays_pending():
    ui = _confirmation_ui()
    pending = ui.session_state['workflow-form-draft:demo']
    restored_pending = _confirmation_ui(form=pending)
    assert not restored_pending.exception
    assert not restored_pending.button(key='node-model-confirm:demo:sft').disabled
    ui.button(key='node-model-confirm:demo:sft').click().run()
    saved = ui.session_state['workflow-form-draft:demo']
    restored = _confirmation_ui(form=saved)
    assert not restored.exception
    assert restored.button(key='node-model-confirm:demo:sft').disabled
    assert restored.session_state['workflow-node-model-confirmations:demo'] == saved['workflow-node-model-confirmations:demo']


def test_legacy_saved_model_choices_do_not_require_reconfirmation():
    binding = {'backend': 'writer', 'model': 'alpha', **TOKEN_LIMITS}
    saved = {'workflow-node-bindings:demo': {'sft': {'generation': binding, 'jev': dict(binding)}}}
    ui = _confirmation_ui(form=saved)
    assert not ui.exception
    assert ui.button(key='node-model-confirm:demo:sft').disabled
    assert 'sft' in ui.session_state['workflow-form-draft:demo']['workflow-node-model-confirmations:demo']


def test_incomplete_model_selection_cannot_be_confirmed():
    ui = _confirmation_ui(roles={})
    assert not ui.exception
    assert ui.button(key='node-model-confirm:demo:sft').disabled
    assert any(item.value == '待确认 · 请补全模型配置' for item in ui.caption)
    assert ui.session_state['workflow-node-model-confirmations:demo'] == {}


def test_confirmation_rechecks_service_existence_at_click_time():
    ui = _confirmation_ui()
    ui.session_state['fixture-local'] = {'backends': {}}
    ui.button(key='node-model-confirm:demo:sft').click().run()
    assert not ui.exception
    assert ui.session_state['workflow-node-model-confirmations:demo'] == {}


@pytest.mark.parametrize('legacy_package_binding', [False, True])
@pytest.mark.parametrize('clear_field', ['model:writer', 'backend'])
def test_cleared_jev_model_stays_empty_after_disabled_draft_restart(legacy_package_binding, clear_field):
    script = SCRIPT[:SCRIPT.index('endpoints =')]
    script = script.replace("return {'backends': {}, 'default_backend': ''}",
                            "return {'backends': {}, 'default_backend': 'writer', 'default_model': 'alpha'}")
    script += '''
from lib.application.workflow_node_models_service import WorkflowNodeModelsApplication
from lib.presentation.streamlit.workflow_node_settings import node_bindings
enabled = st.checkbox('启用 JEV', key='fixture-jev-enabled')
review = {'enabled': enabled, 'node': 'jev'}
bindings, endpoints = node_bindings(WorkflowNodeModelsApplication(application),
    ['jev'] if enabled else [], '文档资料', 'demo', package_review=review)
if enabled:
    render_node_models('jev', '文档资料', 'demo', bindings, endpoints,
                       backend_application=application, package_review=review)
'''
    local = {'backends': {
        'writer': {'base_url': 'https://models.example.test/v1', 'models': ['alpha']},
    }}
    ui = AppTest.from_string(script)
    ui.session_state['fixture-local'] = deepcopy(local)
    ui.session_state['fixture-jev-enabled'] = True
    if legacy_package_binding:
        ui.session_state['workflow-form-draft:demo'] = {'workflow-node-bindings:demo': {
            'package': {'jev': {'backend': 'writer', 'model': 'alpha', **TOKEN_LIMITS}},
        }}
    ui.run()
    assert not ui.exception
    assert ui.selectbox(key='node-model:demo:jev:jev:model:writer').value == 'alpha'
    ui.selectbox(key='node-model:demo:jev:jev:' + clear_field).set_value(None).run()
    assert not ui.exception
    assert ui.session_state['workflow-node-bindings:demo']['jev'] == {}
    ui.checkbox(key='fixture-jev-enabled').uncheck().run()
    saved = deepcopy(ui.session_state['workflow-form-draft:demo'])
    assert saved['workflow-node-bindings:demo']['jev'] == {}
    restarted = AppTest.from_string(script)
    restarted.session_state['fixture-local'] = deepcopy(local)
    restarted.session_state['workflow-form-draft:demo'] = saved
    restarted.run()
    assert not restarted.exception
    assert 'jev:jev' in restarted.session_state['workflow-node-bindings:demo:initialized']
    restarted.checkbox(key='fixture-jev-enabled').check().run()
    assert not restarted.exception
    assert restarted.session_state['workflow-node-bindings:demo']['jev'] == {}
    assert restarted.selectbox(key='node-model:demo:jev:jev:backend').value is None
    assert restarted.button(key='node-model-confirm:demo:jev').disabled
    if legacy_package_binding:
        assert restarted.session_state['workflow-node-bindings:demo']['package']['jev']['model'] == 'alpha'


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


DOCUMENT_SCRIPT = SCRIPT.replace('render_node_models\n', 'render_document_parser\n').replace(
    "render_node_models('sft', '文档资料', 'demo', bindings, endpoints,\n"
    "                   backend_application=application)",
    "st.session_state['fixture-parser'] = render_document_parser('demo', bindings, endpoints,\n"
    "                   backend_application=application)",
)


def _document_ui(script=DOCUMENT_SCRIPT):
    ui = AppTest.from_string(script)
    ui.session_state['fixture-local'] = {'backends': {
        'writer': {'base_url': 'https://models.example.test/v1', 'models': ['alpha', 'beta'],
                   'api_key_env': 'WRITER_KEY'},
    }}
    ui.session_state['workflow-node-bindings:demo'] = {'ingest': {
        'vision': {'backend': 'writer', 'model': 'alpha', **TOKEN_LIMITS},
    }}
    return ui.run()


def test_document_node_defaults_to_model_assisted_parsing_and_can_choose_local():
    ui = _document_ui()
    assert not ui.exception
    assert ui.session_state['fixture-parser'] == {'mode': 'model'}
    assert any(field.label == '模型服务' for field in ui.selectbox)
    ui.radio(key='workflow-document-parse-mode:demo').set_value('native').run()
    assert not ui.exception
    assert ui.session_state['fixture-parser'] == {'mode': 'native'}
    assert not ui.selectbox
    assert not ui.button


def test_document_node_requires_user_confirmation_for_exact_model_and_connection():
    ui = _document_ui()
    ui.radio(key='workflow-document-parse-mode:demo').set_value('vision').run()
    assert not ui.exception
    assert ui.session_state['fixture-parser']['unconfirmed'] is True
    assert ui.button(key='node-vision-save:demo').disabled
    next(box for box in ui.checkbox if box.label == '我已核实所选模型支持图片输入').check().run()
    ui.button(key='node-vision-save:demo').click().run()
    assert not ui.exception
    assert 'unconfirmed' not in ui.session_state['fixture-parser']
    capabilities = ui.session_state['fixture-local']['backends']['writer']['model_capabilities']
    assert capabilities['alpha']['vision'] is True
    assert 'beta' not in capabilities
    ui.selectbox(key='node-model:demo:ingest:vision:model:writer').set_value('beta').run()
    assert ui.session_state['fixture-parser']['unconfirmed'] is True
    assert ui.button(key='node-vision-save:demo').disabled
    ui.selectbox(key='node-model:demo:ingest:vision:model:writer').set_value('alpha').run()
    local = ui.session_state['fixture-local']
    local['backends']['writer']['base_url'] = 'https://replacement.example.test/v1'
    ui.session_state['fixture-local'] = local
    ui.run()
    assert not ui.exception
    assert ui.session_state['fixture-parser']['unconfirmed'] is True
    assert ui.button(key='node-vision-save:demo').disabled


DOCUMENT_NAVIGATION_SCRIPT = DOCUMENT_SCRIPT.replace(
    'from lib.presentation.streamlit.workflow_node_settings import render_document_parser',
    'from lib.presentation.streamlit.workflow_node_settings import render_document_parser, document_parser_mode',
).replace(
    "st.session_state['fixture-parser'] = render_document_parser('demo', bindings, endpoints,\n"
    "                   backend_application=application)",
    "page = st.radio('Page', ('workflow', 'work_management'), key='fixture-page')\n"
    "creation = st.radio('Creation', ('auto', 'manual'), key='fixture-creation')\n"
    "node = st.radio('Node', ('ingest', 'sft'), key='fixture-node')\n"
    "st.session_state['fixture-active-parser-mode'] = document_parser_mode('demo')\n"
    "if page == 'workflow' and creation == 'auto' and node == 'ingest':\n"
    "    st.session_state['fixture-parser'] = render_document_parser('demo', bindings, endpoints,\n"
    "                       backend_application=application)",
)


@pytest.mark.parametrize('control, away, original', [
    ('fixture-node', 'sft', 'ingest'),
    ('fixture-creation', 'manual', 'auto'),
    ('fixture-page', 'work_management', 'workflow'),
])
def test_document_parser_mode_survives_widget_cleanup_and_restores_on_return(control, away, original):
    ui = _document_ui(DOCUMENT_NAVIGATION_SCRIPT)
    ui.radio(key='workflow-document-parse-mode:demo').set_value('vision').run()
    assert not ui.exception
    assert ui.session_state['workflow-document-parse-mode-draft:demo'] == 'vision'
    ui.radio(key=control).set_value(away).run()
    # A real navigation removes the radio and lets Streamlit discard its key.
    assert 'workflow-document-parse-mode:demo' not in ui.session_state
    ui.run()
    assert ui.session_state['fixture-active-parser-mode'] == 'vision'
    assert ui.session_state['workflow-document-parse-mode-draft:demo'] == 'vision'
    ui.radio(key=control).set_value(original).run()
    assert not ui.exception
    assert ui.radio(key='workflow-document-parse-mode:demo').value == 'vision'
    assert ui.session_state['fixture-parser']['mode'] == 'vision'
    assert ui.session_state['fixture-parser']['binding']['model'] == 'alpha'
    assert ui.session_state['fixture-parser']['unconfirmed'] is True
    # Returning to native must replace the saved choice, not resurrect vision later.
    ui.radio(key='workflow-document-parse-mode:demo').set_value('native').run()
    ui.radio(key=control).set_value(away).run()
    ui.radio(key=control).set_value(original).run()
    assert ui.radio(key='workflow-document-parse-mode:demo').value == 'native'
    assert ui.session_state['fixture-parser'] == {'mode': 'native'}
