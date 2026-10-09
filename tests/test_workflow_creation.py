from unittest.mock import Mock
import pytest
from lib.application.workflow_service import WorkflowApplication
from lib.domain.workflow_creation import validate_creation


@pytest.mark.parametrize('field,value', [
    ('sample_count', True), ('sample_count', 100001), ('concurrency', 0),
    ('batch_size', 501), ('conversation_turns', 1), ('brief', 42),
    ('targets', ['unknown']), ('node_models', {'package': {'generation': {}}}),
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


@pytest.mark.parametrize('targets', [None, 7, True, 'sft', b'sft', {'sft': True}, [['sft']], [None]])
def test_malformed_target_collection_is_a_configuration_error(targets):
    driver = Mock()
    with pytest.raises(ValueError):
        WorkflowApplication(driver).create_run(targets=targets)
    driver.create.assert_not_called()


@pytest.mark.parametrize('mode', ['unsupported', None, {}, True])
def test_invalid_replay_mode_rejected_before_driver(mode):
    driver = Mock()
    with pytest.raises(ValueError, match='invalid_agent_replay_mode'):
        WorkflowApplication(driver).create_run(agent_replay_mode=mode)
    driver.create.assert_not_called()


def test_evaluation_reference_requires_cpt_before_driver():
    driver = Mock()
    with pytest.raises(ValueError):
        WorkflowApplication(driver).create_run(targets=['sft'], evaluation_sources=['missing.jsonl'])
    driver.create.assert_not_called()


def test_direct_engine_rejects_mode_before_reading_sources_or_reference_catalog(tmp_path, monkeypatch):
    from lib.infrastructure import training_workflow as engine
    catalog = Mock(side_effect=AssertionError('reference catalog must not be read'))
    monkeypatch.setattr(engine, 'snapshot_released_corpus', catalog)
    with pytest.raises(ValueError, match='invalid_agent_replay_mode'):
        engine.create_run(tmp_path, sources=[tmp_path / 'missing.txt'], agent_replay_mode='unsupported')
    catalog.assert_not_called()
    assert list(tmp_path.iterdir()) == []


def test_generator_targets_are_normalized_once_before_driver():
    driver = Mock()
    WorkflowApplication(driver).create_run(targets=(item for item in ['sft', 'orpo', 'sft']))
    assert driver.create.call_args.kwargs['targets'] == ['sft', 'orpo']


@pytest.mark.parametrize('targets,version', [(['sft', 'cot'], 15), (['sft'], 11), (['cot'], 11)])
def test_cot_sft_route_is_frozen_in_new_recipe_version(tmp_path, targets, version):
    from lib.infrastructure import training_workflow as engine
    source = tmp_path / 'source.txt'
    source.write_text('设备维护前先断电，再检查线路，完成后记录结果。', encoding='utf-8')
    output = tmp_path / 'output'
    run_id = engine.create_run(output, sources=[source], targets=targets)
    recipe = engine.read_json(engine.run_path(output, run_id) / 'recipe.json')
    assert recipe['version'] == version
    assert recipe['version'] in engine.SUPPORTED_RECIPE_VERSIONS
