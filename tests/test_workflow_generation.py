"""Optional generation styles stay reproducible and do not leak invalid recipes."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from lib.application.workflow_service import WorkflowApplication
from lib.domain.workflow_creation import validate_creation
from lib.domain.workflow_generation import STYLE_PRESETS, style_for_sample, validate_node_generation


def test_old_creation_contract_keeps_optional_generation_absent():
    assert validate_node_generation(None) == {}
    assert validate_creation() == (['cpt', 'sft', 'dpo'], {})


def test_explicit_api_style_is_enabled_and_independent_of_input():
    original = {'sft': {'style': 'concise', 'instruction': '  Explain the assumptions.  '},
                'cot': {'enabled': False, 'style': 'custom'}}
    before = deepcopy(original)
    driver = Mock()
    WorkflowApplication(driver).create_run(targets=['sft', 'cot'], node_generation=original)
    result = driver.create.call_args.kwargs['node_generation']
    assert result['sft'] == {'enabled': True, 'style': 'concise', 'instruction': 'Explain the assumptions.'}
    assert result['cot'] == {'enabled': False, 'style': 'custom', 'instruction': ''}
    result['sft']['instruction'] = 'Changed after creation'
    assert original == before


@pytest.mark.parametrize('value', [
    False, [], 'concise', {'trim': {}}, {'sft': []}, {'sft': {'preset': 'concise'}},
    {'cot': {'enabled': 1}}, {'sft': {'style': True}}, {'sft': {'style': ['concise']}},
    {'sft': {'style': 'unknown'}}, {'sft': {'instruction': None}},
    {'cot': {'instruction': 'x' * 4001}}, {'cot': {'style': 'custom', 'instruction': '  '}},
    {'sft': {'instruction': 'api_key=abcdefghijklmnop'}}, {'sft': {'instruction': '\ufffd'}},
    {'sft': {'enabled': False, 'instruction': '\x00'}},
])
def test_invalid_generation_never_reaches_driver(value):
    driver = Mock()
    with pytest.raises(ValueError, match='invalid_node_generation'):
        WorkflowApplication(driver).create_run(node_generation=value)
    driver.create.assert_not_called()


def test_mixed_styles_remain_identical_across_batch_order_and_retries():
    config = {'style': 'mixed', 'instruction': 'Use the source evidence.'}
    before = deepcopy(config)
    identifiers = [f'candidate-{index}' for index in range(100)]
    first = {identifier: style_for_sample(config, identifier) for identifier in identifiers}
    reordered = {identifier: style_for_sample(config, identifier) for identifier in reversed(identifiers)}
    assert first == reordered
    assert {value['preset'] for value in first.values()} == {
        'concise', 'structured', 'skeptical', 'reflective'}
    for value in first.values():
        assert value['instruction'].startswith(STYLE_PRESETS[value['preset']])
        assert value['instruction'].endswith('Use the source evidence.')
    assert config == before


def test_custom_prompt_is_used_without_an_unrequested_preset():
    assert style_for_sample({'style': 'custom', 'instruction': 'Describe a verifiable explanation.'}, 'sample') == {
        'preset': 'custom', 'instruction': 'Describe a verifiable explanation.'}


def test_cot_and_trim_accept_independent_generation_and_judge_models():
    from lib.application.workflow_node_models_service import WorkflowNodeModelsApplication
    from tests.test_workflow_node_models import Inventory

    application = WorkflowNodeModelsApplication(Inventory())
    draft, _, _ = application.prepare_draft(['cot', 'trim'], '文档', {}, [])
    result = application.snapshot(['cot', 'trim'], '文档', draft)
    assert set(result) == {'cot', 'trim'}
    for node in result.values():
        assert node['generation']['backend'] == 'writer'
        assert node['jev']['backend'] == 'review'
