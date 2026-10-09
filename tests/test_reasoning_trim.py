"""Trimming is opt-in, bounded, and preserves the answer-editing boundary."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from lib.application.workflow_service import WorkflowApplication
from lib.domain.reasoning_trim import TRIM_TEMPLATES, trim_prompt, validate_reasoning_trim


def test_missing_and_disabled_trim_do_not_supply_editing_instructions():
    assert validate_reasoning_trim(None) is None
    assert trim_prompt(None) == ''
    assert trim_prompt({}) == ''
    assert validate_reasoning_trim({'template': 'custom'}) == {
        'enabled': False, 'template': 'custom', 'instruction': '', 'custom_prompt': ''}


def test_builtin_and_custom_trim_keep_necessary_evidence_and_final_answer_boundary():
    for name in TRIM_TEMPLATES:
        prompt = trim_prompt({'enabled': True, 'template': name})
        assert '不得改变最终答案' in prompt
        assert '必要的事实、引用、来源依据' in prompt
        assert '不得删除正常知识问答中的合法内容' in prompt
    prompt = trim_prompt({'enabled': True, 'template': 'custom',
                         'custom_prompt': 'Remove repeated headings.', 'instruction': 'Keep citations.'})
    assert prompt.startswith('Remove repeated headings.')
    assert '不得改变最终答案' in prompt
    assert prompt.endswith('Keep citations.')


def test_trim_application_snapshot_is_normalized_and_does_not_modify_form():
    original = {'enabled': True, 'instruction': '  Keep citations.  '}
    before = deepcopy(original)
    driver = Mock()
    WorkflowApplication(driver).create_run(targets=['sft'], reasoning_trim=original)
    assert driver.create.call_args.kwargs['reasoning_trim'] == {
        'enabled': True, 'template': 'leakage', 'instruction': 'Keep citations.', 'custom_prompt': ''}
    assert original == before


@pytest.mark.parametrize('value', [
    [], False, 'leakage', {'extra': True}, {'enabled': 1}, {'template': []},
    {'template': 'unknown'}, {'instruction': None}, {'custom_prompt': None},
    {'instruction': 'x' * 4001}, {'custom_prompt': 'x' * 16001},
    {'enabled': True, 'template': 'custom'},
    {'instruction': 'api_key=abcdefghijklmnop'}, {'custom_prompt': '\ufffd'},
    {'enabled': False, 'custom_prompt': '\x00'},
])
def test_invalid_trim_is_rejected_before_driver(value):
    driver = Mock()
    with pytest.raises(ValueError, match='invalid_reasoning_trim'):
        WorkflowApplication(driver).create_run(reasoning_trim=value)
    driver.create.assert_not_called()


@pytest.mark.parametrize('targets', [['cpt'], ['dpo'], ['orpo', 'rlaif'], ['multiturn']])
def test_trim_requires_an_explicit_reasoning_output_target(targets):
    driver = Mock()
    with pytest.raises(ValueError, match='reasoning_trim_requires_reasoning_target'):
        WorkflowApplication(driver).create_run(targets=targets, reasoning_trim={'enabled': True})
    driver.create.assert_not_called()
