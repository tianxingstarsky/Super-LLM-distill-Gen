"""Offline canonical controls freeze provider maps and retain native legacy choices."""
from copy import deepcopy

import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


SCRIPT = '''
import streamlit as st
from copy import deepcopy
from lib.presentation.streamlit.i18n import install_streamlit_localization
from lib.presentation.streamlit.workflow_node_settings import render_node_models
install_streamlit_localization()
class Application:
    def get_model_info(self, backend, model):
        return deepcopy(st.session_state.get('fixture-info', {}).get(model, {}))
    def list_backends(self):
        return {'backends': [endpoint]}
    def discover_models(self, name, overrides=None):
        st.session_state['fixture-discovery-calls'] = st.session_state.get('fixture-discovery-calls', 0) + 1
        st.session_state['fixture-info'] = deepcopy(st.session_state['fixture-refreshed-info'])
        return {'ok': True, 'models': ['alpha', 'beta', 'unknown'], 'model_info': st.session_state['fixture-info']}
endpoint = {'name': 'writer', 'base_url': 'https://example.test/v1',
            'api_format': st.session_state.get('fixture-api', 'chat'), 'models': ['alpha', 'beta', 'unknown']}
form = st.session_state.get('workflow-form-draft:demo', {})
bindings = deepcopy(st.session_state.get('workflow-node-bindings:demo', form.get('workflow-node-bindings:demo', {})))
st.session_state['workflow-node-scope:demo'] = {'nodes': ['sft', 'cot'], 'source_mode': '文档资料'}
node = st.selectbox('Fixture node', ['sft', 'cot'], key='fixture-node')
render_node_models(node, '文档资料', 'demo', bindings, {'writer': endpoint}, backend_application=Application())
'''

PREFIX = 'node-model:demo:sft:generation'
RKEY = PREFIX + ':reasoning:writer:alpha'
EFFORTS = ['low', 'medium', 'high', 'xhigh', 'max']
SCHEMA = 'shujian.reasoning-effort/v1'
CHOICES = {'reasoning_efforts': ['low', 'high', 'max'], 'default_reasoning_effort': 'high',
           'reasoning_disabled': True}
BASE = {'backend': 'writer', 'model': 'alpha', 'context_window_tokens': 16_384, 'max_output_tokens': 8192}


def ui_fixture(*, info=None, reasoning=None, api_format='chat', language='zh'):
    ui = AppTest.from_string(SCRIPT, default_timeout=15)
    ui.session_state['ui_language'] = language
    ui.session_state['fixture-api'] = api_format
    ui.session_state['fixture-info'] = deepcopy(info if info is not None else {'alpha': CHOICES})
    binding = {**BASE, **({'reasoning': reasoning} if reasoning is not None else {})}
    ui.session_state['workflow-node-bindings:demo'] = {'sft': {'generation': binding, 'jev': deepcopy(BASE)}}
    ui.run()
    assert not ui.exception
    return ui


def binding(ui):
    return ui.session_state['workflow-node-bindings:demo']['sft']['generation']


def test_declared_efforts_default_selection_and_confirmation_are_persisted():
    ui = ui_fixture()
    mode = ui.selectbox(key=RKEY + ':mode')
    assert mode.options == ['自动（服务默认）', '开启思考', '关闭思考']
    assert 'reasoning' not in binding(ui)
    assert 'fixture-discovery-calls' not in ui.session_state
    ui.button(key='node-model-confirm:demo:sft').click().run()
    assert ui.button(key='node-model-confirm:demo:sft').disabled
    ui.selectbox(key=RKEY + ':mode').set_value('enabled').run()
    assert not ui.exception
    assert ui.selectbox(key=RKEY + ':effort').options == EFFORTS
    assert ui.selectbox(key=RKEY + ':effort').value == 'medium'
    selected = binding(ui)['reasoning']
    assert selected['schema'] == SCHEMA and selected['effort'] == 'medium'
    assert selected['provider_mapping'] == {
        'low': {'mode': 'enabled', 'effort': 'low'},
        'medium': {'mode': 'enabled', 'effort': 'high'},
        'high': {'mode': 'enabled', 'effort': 'high'},
        'xhigh': {'mode': 'enabled', 'effort': 'max'},
        'max': {'mode': 'enabled', 'effort': 'max'},
    }
    assert any('medium / high' in item.value and '原生支持五档' in item.value for item in ui.caption)
    assert not ui.button(key='node-model-confirm:demo:sft').disabled
    ui.selectbox(key=RKEY + ':effort').set_value('xhigh').run()
    ui.number_input(key=PREFIX + ':context:writer:alpha').set_value(20_000).run()
    assert binding(ui)['reasoning']['effort'] == 'xhigh'
    assert binding(ui)['reasoning']['provider_mapping']['xhigh'] == {'mode': 'enabled', 'effort': 'max'}
    assert ui.session_state['workflow-form-draft:demo']['workflow-node-bindings:demo']['sft']['generation'] == binding(ui)


def test_unknown_capabilities_do_not_invent_levels_and_saved_override_can_reset():
    ui = ui_fixture(info={})
    assert ui.selectbox(key=RKEY + ':mode').options == ['自动（服务默认）']
    assert not any(item.label == '思考强度' for item in ui.selectbox)
    ui = ui_fixture(info={}, reasoning={'mode': 'enabled', 'effort': 'custom_effort'})
    assert ui.selectbox(key=RKEY + ':mode').value == 'saved'
    assert binding(ui)['reasoning'] == {'mode': 'enabled', 'effort': 'custom_effort'}
    ui.number_input(key=PREFIX + ':output:writer:alpha').set_value(6000).run()
    assert binding(ui)['reasoning']['effort'] == 'custom_effort'
    ui.selectbox(key=RKEY + ':mode').set_value('auto').run()
    assert 'reasoning' not in binding(ui)


def test_disabled_only_offered_when_declared_and_saved_choice_survives_metadata_change():
    ui = ui_fixture(info={'alpha': {**CHOICES, 'reasoning_disabled': False}})
    assert '关闭思考' not in ui.selectbox(key=RKEY + ':mode').options
    ui = ui_fixture(reasoning={'mode': 'disabled'})
    assert ui.selectbox(key=RKEY + ':mode').value == 'disabled'
    ui.session_state['fixture-info'] = {'alpha': {**CHOICES, 'reasoning_disabled': False}}
    ui.run()
    assert ui.selectbox(key=RKEY + ':mode').value == 'saved'
    assert binding(ui)['reasoning'] == {'mode': 'disabled'}


def test_node_model_switch_reuse_and_page_reopen_preserve_reasoning():
    ui = ui_fixture(reasoning={'mode': 'enabled', 'effort': 'extreme'})
    ui.selectbox(key=PREFIX + ':model:writer').set_value('beta').run()
    assert 'reasoning' not in binding(ui)
    ui.selectbox(key=PREFIX + ':model:writer').set_value('alpha').run()
    assert binding(ui)['reasoning'] == {'mode': 'enabled', 'effort': 'extreme'}
    assert binding(ui)['max_output_tokens'] == BASE['max_output_tokens']
    ui.selectbox(key='fixture-node').set_value('cot').run()
    ui.selectbox(key='fixture-node').set_value('sft').run()
    assert binding(ui)['reasoning']['effort'] == 'extreme'
    ui.button(key='node-model:demo:sft:jev:reuse-generation').click().run()
    assert ui.session_state['workflow-node-bindings:demo']['sft']['jev']['reasoning'] == binding(ui)['reasoning']
    restarted = AppTest.from_string(SCRIPT, default_timeout=15)
    restarted.session_state['fixture-info'] = {'alpha': CHOICES}
    restarted.session_state['workflow-form-draft:demo'] = deepcopy(ui.session_state['workflow-form-draft:demo'])
    restarted.run()
    assert not restarted.exception
    assert binding(restarted)['reasoning']['effort'] == 'extreme'


def test_anthropic_budget_uses_canonical_five_levels_and_capped_frozen_mapping():
    ui = ui_fixture(api_format='anthropic', info={'alpha': {'reasoning_format': 'anthropic_budget'}})
    ui.selectbox(key=RKEY + ':mode').set_value('enabled').run()
    assert ui.selectbox(key=RKEY + ':effort').options == EFFORTS
    assert not any(item.key == RKEY + ':budget_tokens' for item in ui.number_input)
    selected = deepcopy(binding(ui)['reasoning'])
    assert selected['effort'] == 'medium'
    assert selected['provider_mapping'] == {
        'low': {'mode': 'enabled', 'budget_tokens': 1024},
        'medium': {'mode': 'enabled', 'budget_tokens': 2048},
        'high': {'mode': 'enabled', 'budget_tokens': 4096},
        'xhigh': {'mode': 'enabled', 'budget_tokens': 8191},
        'max': {'mode': 'enabled', 'budget_tokens': 8191},
    }
    assert any('2048 tokens' in item.value for item in ui.caption)
    ui.number_input(key=PREFIX + ':output:writer:alpha').set_value(1024).run()
    assert not ui.exception
    assert binding(ui)['max_output_tokens'] == 8192
    assert binding(ui)['reasoning'] == selected
    assert ui.selectbox(key=RKEY + ':effort').options == EFFORTS


def test_invalid_saved_unknown_budget_remains_resettable_without_crashing():
    ui = ui_fixture(info={}, reasoning={'mode': 'enabled', 'budget_tokens': 9000})
    assert ui.selectbox(key=RKEY + ':mode').value == 'saved'
    assert any('思考预算必须小于单次输出上限' in item.value for item in ui.warning)
    ui.selectbox(key=RKEY + ':mode').set_value('auto').run()
    assert not ui.exception
    assert 'reasoning' not in binding(ui)


def test_refresh_capabilities_is_explicit_and_unlocks_only_returned_levels():
    ui = ui_fixture(info={})
    ui.session_state['fixture-refreshed-info'] = {'alpha': CHOICES}
    ui.button(key=RKEY + ':discover').click().run()
    assert not ui.exception
    assert ui.session_state['fixture-discovery-calls'] == 1
    assert ui.selectbox(key=RKEY + ':mode').value == 'auto'
    ui.selectbox(key=RKEY + ':mode').set_value('enabled').run()
    assert ui.selectbox(key=RKEY + ':effort').options == EFFORTS


def test_removed_saved_effort_remains_visible_instead_of_silent_downgrade():
    ui = ui_fixture(reasoning={'mode': 'enabled', 'effort': 'extreme'})
    ui.session_state['fixture-info'] = {'alpha': {**CHOICES, 'reasoning_efforts': ['low', 'high']}}
    ui.run()
    assert not ui.exception
    assert ui.selectbox(key=RKEY + ':mode').value == 'saved'
    assert binding(ui)['reasoning']['effort'] == 'extreme'


def test_legacy_enabled_without_effort_upgrades_only_after_explicit_mode_change():
    ui = ui_fixture(info={'alpha': {**CHOICES, 'reasoning_format': 'deepseek'}}, reasoning={'mode': 'enabled'})
    assert binding(ui)['reasoning'] == {'mode': 'enabled'}
    assert ui.selectbox(key=RKEY + ':mode').value == 'saved'
    assert not any(item.key == RKEY + ':effort' for item in ui.selectbox)
    ui.selectbox(key=RKEY + ':mode').set_value('enabled').run()
    assert not ui.exception
    assert binding(ui)['reasoning']['schema'] == SCHEMA
    assert binding(ui)['reasoning']['effort'] == 'medium'
    assert ui.selectbox(key=RKEY + ':effort').options == EFFORTS


def test_supported_legacy_native_effort_is_preserved_until_explicit_upgrade():
    original = {'mode': 'enabled', 'effort': 'low'}
    ui = ui_fixture(reasoning=original)
    assert ui.selectbox(key=RKEY + ':mode').value == 'saved'
    ui.number_input(key=PREFIX + ':context:writer:alpha').set_value(20_000).run()
    assert binding(ui)['reasoning'] == original
    ui.selectbox(key=RKEY + ':mode').set_value('enabled').run()
    assert binding(ui)['reasoning']['schema'] == SCHEMA
    assert binding(ui)['reasoning']['effort'] == 'medium'


@pytest.mark.parametrize('api_format,scheme', [('chat', 'deepseek'), ('anthropic', 'anthropic_adaptive')])
def test_declared_toggle_maps_all_five_levels_to_enabled(api_format, scheme):
    ui = ui_fixture(api_format=api_format, info={'alpha': {'reasoning_format': scheme}})
    ui.selectbox(key=RKEY + ':mode').set_value('enabled').run()
    assert not ui.exception
    assert ui.selectbox(key=RKEY + ':effort').options == EFFORTS
    assert binding(ui)['reasoning']['provider_mapping'] == {effort: {'mode': 'enabled'} for effort in EFFORTS}
    assert any('low / medium / high / xhigh / max' in item.value for item in ui.caption)


def test_unknown_provider_vocabulary_is_not_guessed():
    ui = ui_fixture(info={'alpha': {'reasoning_efforts': ['gentle', 'intense']}})
    assert ui.selectbox(key=RKEY + ':mode').options == ['自动（服务默认）']
    assert 'reasoning' not in binding(ui)


def test_canonical_mapping_survives_capability_refresh_and_unknown_metadata():
    ui = ui_fixture()
    ui.selectbox(key=RKEY + ':mode').set_value('enabled').run()
    original = deepcopy(binding(ui)['reasoning'])
    ui.session_state['fixture-info'] = {'alpha': {'reasoning_efforts': ['low', 'max']}}
    ui.run()
    assert binding(ui)['reasoning'] == original
    ui.session_state['fixture-info'] = {}
    ui.selectbox(key=RKEY + ':effort').set_value('high').run()
    assert not ui.exception
    assert binding(ui)['reasoning']['effort'] == 'high'
    assert binding(ui)['reasoning']['provider_mapping'] == original['provider_mapping']


def test_callback_uses_cached_protocol_and_capabilities(monkeypatch):
    from types import SimpleNamespace
    from lib.presentation.streamlit import workflow_reasoning_controls as controls
    state = {
        RKEY + ':mode': 'enabled', RKEY + ':standard_ui': True,
        RKEY + ':api_format': 'anthropic',
        RKEY + ':capabilities': {'reasoning_format': 'anthropic_budget'},
    }
    monkeypatch.setattr(controls, 'st', SimpleNamespace(session_state=state))
    choice = controls.selected_reasoning(PREFIX, 'writer', 'alpha', {}, 8192)
    assert choice['schema'] == SCHEMA and choice['effort'] == 'medium'
    assert choice['provider_mapping']['medium'] == {'mode': 'enabled', 'budget_tokens': 2048}


def test_all_reasoning_control_phrases_have_english_translations():
    import ast
    from pathlib import Path
    import re
    from lib.presentation.streamlit.workflow_reasoning_controls import reasoning_label
    source = Path(__file__).resolve().parents[1] / 'lib/presentation/streamlit/workflow_reasoning_controls.py'
    phrases = [node.value for node in ast.walk(ast.parse(source.read_text(encoding='utf-8')))
               if isinstance(node, ast.Constant) and isinstance(node.value, str)
               and re.search(r'[\u4e00-\u9fff]', node.value)]
    for phrase in phrases:
        assert not re.search(r'[\u4e00-\u9fff]', reasoning_label(phrase, 'en')), phrase


def test_english_controls_and_workflow_error_and_snapshot_translation():
    ui = ui_fixture(language='en')
    assert ui.selectbox(key=RKEY + ':mode').label == 'Model reasoning'
    assert ui.selectbox(key=RKEY + ':mode').options == [
        'Automatic (service default)', 'Enable reasoning', 'Disable reasoning']
    ui.selectbox(key=RKEY + ':mode').set_value('enabled').run()
    assert any('Actual model setting' in item.value for item in ui.caption)
    assert any('not five native model levels' in item.value for item in ui.caption)
    from lib.presentation.streamlit.workflow_page import _workflow_error, _stage_configuration
    from lib.presentation.streamlit.workflow_reasoning_controls import REASONING_ERRORS, reasoning_summary
    from lib.presentation.streamlit.i18n import translate
    for code, chinese in REASONING_ERRORS.items():
        assert _workflow_error(ValueError(code)) == chinese
        assert translate(chinese, 'en') != chinese
    assert reasoning_summary({'mode': 'enabled', 'effort': 'extreme'}, language='en') == 'Enable reasoning · extreme'
    recipe = {'targets': ['sft'], 'node_models': {'sft': {'generation': {**BASE,
        'reasoning': {'mode': 'enabled', 'effort': 'extreme'}}}}}
    details = _stage_configuration('sft', recipe, {})
    assert 'extreme' in details['生成思考模式']
