from unittest.mock import Mock
import pytest
from lib.application.workflow_service import WorkflowApplication
from lib.domain.workflow_creation import validate_creation


@pytest.mark.parametrize('field,value', [
    ('sample_count', True), ('sample_count', 100001), ('concurrency', 0),
    ('batch_size', 501), ('conversation_turns', 1), ('brief', 42),
    ('targets', ['unknown']), ('node_models', {'package': {}}),
])
def test_invalid_creation_never_reaches_storage(field, value):
    driver = Mock()
    with pytest.raises(ValueError):
        WorkflowApplication(driver).create_run(**{field: value})
    driver.create.assert_not_called()


def test_large_creation_preserves_recipe_and_copies_node_bindings():
    driver = Mock()
    driver.create.return_value = 'run'
    binding = {'sft': {'generation': {'backend': ' local ', 'model': ' model '}}}
    assert WorkflowApplication(driver).create_run(
        brief='Generate examples', sample_count=50000, targets=['sft', 'orpo', 'sft'],
        node_models=binding, name='Batch', sources=['source.jsonl']) == 'run'
    recipe = driver.create.call_args.kwargs
    assert recipe['targets'] == ['sft', 'orpo']
    assert recipe['sample_count'] == 50000
    assert recipe['sources'] == ['source.jsonl']
    assert recipe['name'] == 'Batch'
    assert recipe['node_models']['sft']['generation']['backend'] == 'local'
    assert binding['sft']['generation']['backend'] == ' local '


def test_domain_defaults_match_existing_creation_contract():
    assert validate_creation() == (['cpt', 'sft', 'dpo'], {})
