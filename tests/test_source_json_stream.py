import json
import tracemalloc
import pytest

from lib.infrastructure.json_stream import iter_json_records, iter_source_json_records


@pytest.mark.parametrize('value,expected', [
    ([], []), ([None, 7, {'text': 'A'}], [None, 7, {'text': 'A'}]),
    ([{'role': 'user', 'content': 'Q'}, {'role': 'assistant', 'content': 'A'}],
     [{'messages': [{'role': 'user', 'content': 'Q'}, {'role': 'assistant', 'content': 'A'}]}]),
    ([{'role': 'user', 'content': 'Q'}, {'text': 'record'}],
     [{'role': 'user', 'content': 'Q'}, {'text': 'record'}]),
    ({'text': 'single'}, [{'text': 'single'}]),
])
def test_source_shapes_keep_existing_record_semantics(tmp_path, value, expected):
    path = tmp_path / 'source.json'
    path.write_text(json.dumps(value), encoding='utf-8')
    assert list(iter_source_json_records(path)) == expected


def test_strict_checkpoint_reader_still_rejects_scalar_elements(tmp_path):
    path = tmp_path / 'source.json'
    path.write_text('[null]', encoding='utf-8')
    with pytest.raises(ValueError, match='invalid_record_array'):
        list(iter_json_records(path))


@pytest.mark.parametrize('content', ['[{"text":"A"},]', '[{"text":"A"}] extra', '[1 2]'])
def test_malformed_dataset_arrays_are_rejected(tmp_path, content):
    path = tmp_path / 'source.json'
    path.write_text(content, encoding='utf-8')
    with pytest.raises(ValueError, match='invalid_record_array'):
        list(iter_source_json_records(path))


def test_fifty_thousand_json_sources_parse_without_dataset_list(tmp_path):
    from lib.infrastructure.training_workflow import Workflow, create_run
    path = tmp_path / 'source.json'
    with path.open('w', encoding='utf-8') as handle:
        handle.write('[')
        for index in range(50000):
            if index:
                handle.write(',')
            json.dump({'text': f'Equipment maintenance instruction number {index}.'}, handle)
        handle.write(']')
    run_id = create_run(tmp_path / 'output', sources=[path], targets=['cpt'])
    run = Workflow(tmp_path / 'output', run_id, tmp_path)
    first = last = None
    count = 0
    tracemalloc.start()
    try:
        for row in run.iter_source_units(run.recipe['sources'][0]):
            first = row if first is None else first
            last = row
            count += 1
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert count == 50000
    assert first['source_location']['record'] == 1
    assert last['source_location']['record'] == 50000
    assert first['status'] == last['status'] == 'ready'
    assert peak < 4 * 1024 * 1024
