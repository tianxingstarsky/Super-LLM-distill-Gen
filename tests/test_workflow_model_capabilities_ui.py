"""Model discovery and evidence stay in the selected workflow node."""
import re

import pytest
from streamlit.testing.v1 import AppTest

from lib.presentation.streamlit.workflow_model_capabilities import suggested_tokens


SCRIPT = '''
import streamlit as st
from copy import deepcopy
from lib.presentation.streamlit.workflow_node_settings import render_node_models

class Application:
    def get_model_info(self, backend, model):
        return deepcopy(st.session_state.get('fixture-info', {}).get(model, {}))
    def discover_models(self, name, overrides=None):
        st.session_state['fixture-discovery-name'] = name
        st.session_state['fixture-discovery-overrides'] = overrides
        if st.session_state.get('fixture-discovery-error'):
            raise ValueError('fixture discovery failure')
        result = st.session_state.get('fixture-discovery-result', {
            'ok': True, 'models': ['beta', 'alpha'],
            'model_info': st.session_state.get('fixture-discovery-info', st.session_state.get('fixture-info', {}))})
        if overrides is None and result.get('ok', True) and isinstance(result.get('models'), list):
            st.session_state['fixture-discovered'] = result['models']
        return deepcopy(result)
    def test_model(self, backend, model, features=None):
        st.session_state['fixture-test-selection'] = (backend, model, features)
        tests = {feature: {'status': 'passed' if feature in ('text', 'vision') else 'unsupported',
                           'reason_code': 'content_verified' if feature in ('text', 'vision') else 'feature_rejected',
                           'tested_at': '2026-10-09T08:00:00Z'} for feature in features}
        current = st.session_state.setdefault('fixture-info', {}).setdefault(model, {})
        current['probes'] = {'tests': tests, 'tested_at': '2026-10-09T08:00:00Z'}
        if 'vision' in features:
            current['vision'] = True
            current.setdefault('sources', {})['vision'] = 'probe'
        return {'tests': tests}
    def save_model_capabilities(self, backend, model, **values):
        st.session_state['fixture-capability-save'] = (backend, model, values)
        current = st.session_state.setdefault('fixture-info', {}).setdefault(model, {})
        current['manual'] = values
        for field, value in values.items():
            if value is not None:
                current[field] = value
                current.setdefault('sources', {})[field] = 'manual'
    def save_endpoint(self, name, base_url, models, **kwargs):
        st.session_state['fixture-new-endpoint'] = {'name': name, 'base_url': base_url,
                                                   'models': models, **kwargs}
        st.session_state['fixture-empty'] = False

endpoints = {} if st.session_state.get('fixture-empty') else {
    'writer': {'name': 'writer', 'base_url': 'https://models.example.test/v1',
               'api_format': 'chat', 'models': ['alpha'],
               'discovered_models': st.session_state.get('fixture-discovered', [])}
}
new_endpoint = st.session_state.get('fixture-new-endpoint')
if new_endpoint:
    endpoints[new_endpoint['name']] = new_endpoint
bindings = st.session_state.get('workflow-node-bindings:demo', {})
render_node_models('sft', '文档资料', 'demo', bindings, endpoints,
                   backend_application=Application())
'''


def _ui():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['fixture-info'] = {
        'alpha': {'context_window_tokens': 131_072, 'max_output_tokens': 65_536,
                  'vision': None, 'pdf': False, 'tools': True,
                  'sources': {'context_window_tokens': 'models.dev', 'max_output_tokens': 'service',
                              'pdf': 'models.dev', 'tools': 'service'}},
        'beta': {'context_window_tokens': 65_536, 'max_output_tokens': 4_096,
                 'vision': True, 'pdf': None, 'tools': False,
                 'sources': {'vision': 'models.dev'}},
    }
    ui.session_state['workflow-node-bindings:demo'] = {'sft': {'generation': {
        'backend': 'writer', 'model': 'alpha', 'context_window_tokens': 12_000,
        'max_output_tokens': 2_000,
    }}}
    ui.run()
    assert not ui.exception
    return ui


def test_discovery_offers_remote_models_without_replacing_binding_or_limits():
    ui = _ui()
    ui.button(key='node-model:demo:sft:generation:discover').click().run()
    assert not ui.exception
    assert ui.selectbox(key='node-model:demo:sft:generation:model:writer').options == ['alpha', 'beta']
    assert ui.session_state['workflow-node-bindings:demo']['sft']['generation'] == {
        'backend': 'writer', 'model': 'alpha', 'context_window_tokens': 12_000, 'max_output_tokens': 2_000}
    assert ui.session_state['fixture-discovery-overrides'] is None
    assert any(item.value == '模型列表已更新。' for item in ui.success)
    assert any(item.proto.body == '模型列表已更新。' for item in ui.get('toast'))


def test_discovery_keeps_unsaved_capabilities_and_connection_fields():
    ui = _ui()
    prefix = 'node-model:demo:sft:generation'
    ui.selectbox(key=prefix + ':manual:writer:alpha:vision').set_value(True)
    ui.number_input(key=prefix + ':manual:writer:alpha:context_window_tokens').set_value(98_304)
    next(item for item in ui.text_input if item.label == '服务名称').set_value('draft-service')
    next(item for item in ui.text_input if item.label == '服务 API 地址').set_value('https://draft.example.test/v1')
    next(item for item in ui.text_input if item.label == '模型名称').set_value('draft-model')
    ui.button(key=prefix + ':discover').click().run()
    assert not ui.exception
    assert ui.selectbox(key=prefix + ':manual:writer:alpha:vision').value is True
    assert ui.number_input(key=prefix + ':manual:writer:alpha:context_window_tokens').value == 98_304
    assert next(item for item in ui.text_input if item.label == '服务名称').value == 'draft-service'
    assert next(item for item in ui.text_input if item.label == '服务 API 地址').value == 'https://draft.example.test/v1'
    assert next(item for item in ui.text_input if item.label == '模型名称').value == 'draft-model'
    assert ui.selectbox(key=prefix + ':model:writer').options == ['alpha', 'beta']
    assert 'fixture-capability-save' not in ui.session_state
    assert 'fixture-new-endpoint' not in ui.session_state


@pytest.mark.parametrize(('result', 'failed', 'message_type', 'message'), [
    ({'ok': True, 'models': []}, False, 'info', '服务未返回模型列表，仍可手动输入模型名。'),
    ({'ok': True, 'models': ['beta'], 'truncated': True}, False, 'warning', '模型列表过长或响应超时，仅显示已获取的模型。'),
    ({'ok': False, 'models': []}, False, 'warning', '未能获取模型列表，请检查地址、协议和凭据。仍可手动输入模型名。'),
    ({}, True, 'warning', '未能获取模型列表，请检查地址、协议和凭据。仍可手动输入模型名。'),
])
def test_discovery_feedback_survives_full_render_without_changing_selected_model(result, failed, message_type, message):
    ui = _ui()
    ui.session_state['fixture-discovery-result'] = result
    ui.session_state['fixture-discovery-error'] = failed
    ui.button(key='node-model:demo:sft:generation:discover').click().run()
    assert not ui.exception
    assert any(item.value == message for item in ui.get(message_type))
    assert ui.selectbox(key='node-model:demo:sft:generation:model:writer').value == 'alpha'
    assert ui.session_state['workflow-node-bindings:demo']['sft']['generation']['max_output_tokens'] == 2_000
    if result.get('ok'):
        assert any(item.proto.body == message for item in ui.get('toast'))


def test_english_capability_badges_translate_states_and_keep_unknown_source_compact():
    ui = _ui()
    ui.session_state['ui_language'] = 'en'
    ui.run()
    assert not ui.exception
    badge_html = [item.proto.body for item in ui.get('html')
                  if '<span style="display:inline-block' in item.proto.body]
    assert len(badge_html) == 1
    badges = badge_html[0]
    assert 'Images · Unknown' in badges
    assert '>Native PDF · Unsupported</span>' in badges
    assert '>Tool calls · Supported</span>' in badges
    assert 'title="Public metadata"' in badges
    assert 'title="Service information"' in badges
    # Unknown capability evidence does not repeat an unknown provenance label.
    assert badges.count('Unknown') == 1
    assert not re.search(r'[\u3400-\u9fff]', badges)


def test_diagnostics_are_grouped_while_selection_limits_and_confirmation_stay_visible():
    ui = _ui()
    diagnostics = next(item for item in ui.get('popover') if item.proto.popover.label == '模型检测')
    nested_keys = {getattr(item, 'key', None) for item in diagnostics}
    prefix = 'node-model:demo:sft:generation'
    assert prefix + ':discover' in nested_keys
    assert prefix + ':test-features' in nested_keys
    assert prefix + ':test' in nested_keys
    assert prefix + ':manual:writer:alpha:vision' in nested_keys
    assert prefix + ':apply-limits' in nested_keys
    assert [item.label for item in diagnostics.get('tab')] == ['调用检测', '能力设置']
    for key in (prefix + ':backend', prefix + ':model:writer', prefix + ':context:writer:alpha',
                prefix + ':output:writer:alpha', 'node-model-confirm:demo:sft'):
        assert key not in nested_keys
    assert 'fixture-test-selection' not in ui.session_state


def test_discovery_remains_available_after_clearing_model_without_reselecting_it():
    ui = _ui()
    model_key = 'node-model:demo:sft:generation:model:writer'
    ui.selectbox(key=model_key).set_value(None).run()
    assert not ui.exception
    assert not ui.session_state['workflow-node-bindings:demo'].get('sft', {}).get('generation')
    ui.button(key='node-model:demo:sft:generation:discover').click().run()
    assert not ui.exception
    assert ui.selectbox(key=model_key).options == ['alpha', 'beta']
    assert ui.selectbox(key=model_key).value is None
    assert not ui.session_state['workflow-node-bindings:demo'].get('sft', {}).get('generation')
    assert 'fixture-test-selection' not in ui.session_state


def test_new_selection_uses_public_limits_while_saved_limits_stay_unchanged():
    ui = _ui()
    assert ui.number_input(key='node-model:demo:sft:generation:context:writer:alpha').value == 12_000
    ui.button(key='node-model:demo:sft:generation:discover').click().run()
    ui.selectbox(key='node-model:demo:sft:generation:model:writer').set_value('beta').run()
    assert not ui.exception
    binding = ui.session_state['workflow-node-bindings:demo']['sft']['generation']
    assert binding['model'] == 'beta'
    assert binding['context_window_tokens'] == 65_536
    assert binding['max_output_tokens'] == 4_096
    assert any('公开元数据仅作参考，图片输入需调用测试或手动确认。' == item.value for item in ui.caption)


def test_apply_suggestion_is_explicit_and_only_changes_current_node():
    ui = _ui()
    ui.button(key='node-model:demo:sft:generation:apply-limits').click().run()
    assert not ui.exception
    assert ui.session_state['workflow-node-bindings:demo']['sft']['generation'] == {
        'backend': 'writer', 'model': 'alpha', 'context_window_tokens': 131_072, 'max_output_tokens': 32_768}
    assert any('上下文容量 131,072 · 公开元数据' in item.value for item in ui.caption)


def test_probe_selection_and_each_result_are_visible_without_manual_confirmation():
    ui = _ui()
    assert ui.multiselect(key='node-model:demo:sft:generation:test-features').value == ['text', 'vision', 'pdf', 'tools']
    ui.multiselect(key='node-model:demo:sft:generation:test-features').set_value(['text', 'vision', 'pdf']).run()
    ui.button(key='node-model:demo:sft:generation:test').click().run()
    assert not ui.exception
    assert ui.session_state['fixture-test-selection'] == ('writer', 'alpha', ['text', 'vision', 'pdf'])
    assert any('文本 · 通过；图片 · 通过；原生 PDF · 不支持' in item.value for item in ui.caption)
    assert any('原生 PDF：服务拒绝该输入能力' in item.value for item in ui.caption)
    assert any('2026-10-09T08:00:00Z' in item.value for item in ui.caption)
    assert 'fixture-capability-save' not in ui.session_state
    # A connection change invalidates the application's evidence. The old
    # session selection must not resurrect an earlier successful test.
    info = ui.session_state['fixture-info']
    info['alpha']['probes'] = {}
    ui.session_state['fixture-info'] = info
    ui.run()
    assert not any('调用测试：' in item.value for item in ui.caption)


def test_last_probe_skips_are_separate_from_preserved_historical_capability_evidence():
    ui = _ui()
    info = ui.session_state['fixture-info']
    info['alpha']['probes'] = {
        'tests': {'vision': {'status': 'passed'}},
        'last_tests': {'text': {'status': 'failed', 'reason_code': 'authentication_failed'},
                       'vision': {'status': 'skipped', 'reason_code': 'text_connection_failed'}},
        'tested_at': '2026-10-09T08:00:00Z',
    }
    ui.session_state['fixture-info'] = info
    ui.run()
    assert not ui.exception
    assert any('调用测试：文本 · 失败；图片 · 已跳过' in item.value for item in ui.caption)
    assert any('文本：凭据校验失败' in item.value for item in ui.caption)


def test_manual_capability_editor_keeps_unknown_and_separate_evidence():
    ui = _ui()
    ui.selectbox(key='node-model:demo:sft:generation:manual:writer:alpha:vision').set_value(True)
    ui.selectbox(key='node-model:demo:sft:generation:manual:writer:alpha:tools').set_value(False)
    ui.button(key='node-model:demo:sft:generation:save-capabilities').click().run()
    assert not ui.exception
    backend, model, values = ui.session_state['fixture-capability-save']
    assert (backend, model) == ('writer', 'alpha')
    assert values['vision'] is True and values['tools'] is False
    assert values['pdf'] is None
    assert values['context_window_tokens'] is None and values['max_output_tokens'] is None


def test_unsaved_connection_discovery_preserves_form_and_does_not_save_credentials():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['fixture-empty'] = True
    ui.session_state['fixture-discovery-info'] = {'beta': {
        'context_window_tokens': 65_536, 'max_output_tokens': 4_096,
        'sources': {'context_window_tokens': 'service', 'max_output_tokens': 'service'},
    }}
    ui.run()
    next(item for item in ui.text_input if item.label == '服务名称').set_value('custom')
    next(item for item in ui.text_input if item.label == '服务 API 地址').set_value('https://models.example.test/v1')
    next(item for item in ui.text_input if item.label == '环境变量名或密钥').set_value('CUSTOM_KEY')
    next(item for item in ui.button if item.label == '获取模型').click().run()
    assert not ui.exception
    assert 'fixture-new-endpoint' not in ui.session_state
    assert next(item for item in ui.text_input if item.label == '服务 API 地址').value == 'https://models.example.test/v1'
    assert next(item for item in ui.text_input if item.label == '环境变量名或密钥').value == 'CUSTOM_KEY'
    fetched = next(item for item in ui.selectbox if item.label == '获取到的模型')
    fetched.set_value('beta')
    next(item for item in ui.checkbox if item.label == '此服务明确无需按 token 计费').set_value(True)
    next(item for item in ui.button if item.label == '保存并用于当前节点').click().run()
    assert not ui.exception
    assert ui.session_state['fixture-new-endpoint']['models'] == ['beta']
    assert ui.session_state['fixture-new-endpoint']['api_key_env'] == 'CUSTOM_KEY'
    assert ui.session_state['workflow-node-bindings:demo']['sft']['generation'] == {
        'backend': 'custom', 'model': 'beta', 'context_window_tokens': 65_536, 'max_output_tokens': 4_096}


def test_discovered_connection_requires_selection_before_saving():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['fixture-empty'] = True
    ui.run()
    next(item for item in ui.text_input if item.label == '服务名称').set_value('custom')
    next(item for item in ui.text_input if item.label == '服务 API 地址').set_value('https://models.example.test/v1')
    next(item for item in ui.button if item.label == '获取模型').click().run()
    next(item for item in ui.checkbox if item.label == '此服务明确无需按 token 计费').set_value(True)
    next(item for item in ui.button if item.label == '保存并用于当前节点').click().run()
    assert not ui.exception
    assert 'fixture-new-endpoint' not in ui.session_state
    assert any('模型名称' in item.value for item in ui.error)
    assert ('workflow-node-bindings:demo' not in ui.session_state
            or not ui.session_state['workflow-node-bindings:demo'].get('sft'))


def test_unsaved_connection_discovery_limits_do_not_follow_a_changed_connection():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state['fixture-empty'] = True
    ui.session_state['fixture-discovery-info'] = {'beta': {
        'context_window_tokens': 65_536, 'max_output_tokens': 4_096}}
    ui.run()
    next(item for item in ui.text_input if item.label == '服务名称').set_value('custom')
    next(item for item in ui.text_input if item.label == '服务 API 地址').set_value('https://models.example.test/v1')
    next(item for item in ui.button if item.label == '获取模型').click().run()
    assert not ui.exception
    next(item for item in ui.text_input if item.label == '服务 API 地址').set_value('https://replacement.example.test/v1')
    next(item for item in ui.text_input if item.label == '模型名称').set_value('beta')
    next(item for item in ui.checkbox if item.label == '此服务明确无需按 token 计费').set_value(True)
    next(item for item in ui.button if item.label == '保存并用于当前节点').click().run()
    assert not ui.exception
    assert ui.session_state['workflow-node-bindings:demo']['sft']['generation'] == {
        'backend': 'custom', 'model': 'beta', 'context_window_tokens': 131_072, 'max_output_tokens': 32_768}


def test_suggestion_uses_capacity_ceiling_without_defaulting_to_maximum_request_cost():
    assert suggested_tokens({'context_window_tokens': 200_000, 'max_output_tokens': 128_000}) == (200_000, 32_768)
    assert suggested_tokens({'context_window_tokens': 8_192, 'max_output_tokens': 2_048}) == (8_192, 2_048)
    assert suggested_tokens({'context_window_tokens': 2_048}) == (2_048, 2_047)
    assert suggested_tokens({}) == (131_072, 32_768)
