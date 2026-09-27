"""Row checkpoints must remain verifiable, restartable, and bounded in memory."""
import hashlib
import json
import tracemalloc

import pytest

from lib.domain.workflow_quality import canonical
from lib.domain.workflow_scale import generation_units
from lib.infrastructure.workflow_candidates import prepare_generation_rows
from lib.infrastructure.workflow_row_checkpoint import row_checkpoint
from lib.infrastructure.workflow_rows import WorkflowRows, write_jsonl


def noop():
    pass


def test_row_checkpoint_replays_and_rejects_modified_body(tmp_path):
    destination = tmp_path / 'source.json'
    rows = row_checkpoint(destination, lambda: iter([{'status': 'ready', 'id': 'one'}]), noop)
    assert isinstance(rows, WorkflowRows) and len(rows) == 1
    assert list(row_checkpoint(destination, lambda: pytest.fail('must replay'), noop)) == list(rows)
    destination.with_suffix('.jsonl').write_text('{"status":"ready","id":"changed"}\n',encoding='utf-8')
    with pytest.raises(ValueError, match='checkpoint_integrity_error'):
        row_checkpoint(destination, lambda: pytest.fail('must not regenerate corrupt evidence'), noop)


def test_checkpoint_metadata_and_legacy_body_keep_integrity(tmp_path):
    destination = tmp_path / 'source.json'
    rows = [{'status':'ready','id':'legacy'}]
    saved = {'data':rows,'sha256':hashlib.sha256(canonical(rows).encode()).hexdigest()}
    destination.write_text(json.dumps(saved),encoding='utf-8')
    assert row_checkpoint(destination, lambda: pytest.fail('legacy replay'), noop) == rows
    saved['data'][0]['id']='changed'
    destination.write_text(json.dumps(saved),encoding='utf-8')
    with pytest.raises(ValueError, match='checkpoint_integrity_error'):
        row_checkpoint(destination, lambda: iter([]), noop)


def test_changed_row_count_metadata_is_rejected(tmp_path):
    destination=tmp_path/'source.json'
    row_checkpoint(destination,lambda:iter([{'status':'ready'}]),noop)
    saved=json.loads(destination.read_text(encoding='utf-8'))
    saved['data']['count']=50000
    destination.write_text(json.dumps(saved),encoding='utf-8')
    with pytest.raises(ValueError,match='checkpoint_integrity_error'):
        row_checkpoint(destination,lambda:iter([]),noop)


def test_ingest_resume_preserves_input_totals_and_processing_limit(tmp_path,monkeypatch):
    from lib.infrastructure.training_workflow import create_run, Workflow, Cancelled
    source=tmp_path/'source.jsonl'
    source.write_text(''.join(json.dumps({'text':f'Section {i}. Record the source and inspect the device before maintenance.'})+'\n'
                              for i in range(7))+'not json\n',encoding='utf-8')
    output=tmp_path/'output'
    rid=create_run(output,sources=[source],targets=['cpt'],max_units=2,sample_count=2)
    monkeypatch.setattr('lib.infrastructure.training_workflow.prepare_generation_rows',
                        lambda *args:pytest.fail('CPT-only runs must not expand unused model candidates'))
    run=Workflow(output,rid,tmp_path)
    original=run.iter_source_units
    def interrupted(descriptor):
        yield next(original(descriptor))
        raise Cancelled()
    run.iter_source_units=interrupted
    assert run.execute()['status']=='cancelled'
    assert not list((run.path/'checkpoints'/'ingest').glob('*.json'))
    resumed=Workflow(output,rid,tmp_path)
    state=resumed.execute(resume_run=True)
    assert state['status'] in {'completed','needs_attention'}
    summary=state['input_summary']
    assert (summary['units'],summary['ready'],summary['quarantined'],summary['deferred'])==(8,7,1,5)
    assert summary['generation_candidates']==0
    records=json.loads((run.path/'input_records.json').read_text(encoding='utf-8'))
    assert len(records)==8 and records[-1]['location']==8
    selected=list(WorkflowRows(run.path/'stage-results'/'cpt.jsonl',2))
    assert [row['source_location']['record'] for row in selected]==[1,2]


def test_cancelled_checkpoint_has_no_complete_manifest_and_can_retry(tmp_path):
    destination = tmp_path / 'source.json'
    def interrupted():
        yield {'id':'partial'}
        raise RuntimeError('stopped')
    with pytest.raises(RuntimeError, match='stopped'):
        row_checkpoint(destination, interrupted, noop)
    assert not destination.exists()
    assert list(row_checkpoint(destination, lambda: iter([{'id':'complete'}]), noop)) == [{'id':'complete'}]


@pytest.mark.parametrize('count', [None,1,2,3,17])
def test_disk_candidates_match_existing_source_and_variant_contract(tmp_path,count):
    units = [{'id':'doc-one','kind':'document','text':'source one','status':'ready'},
             {'id':'conversation','kind':'conversation','messages':[{'role':'user','content':'context'}],'status':'ready'},
             {'id':'doc-two','kind':'document','text':'source two','status':'ready'}]
    source = tmp_path / 'source.jsonl'
    write_jsonl(source,units)
    rows = prepare_generation_rows(tmp_path/'generated.jsonl',WorkflowRows(source,len(units)),count,noop)
    assert list(rows) == generation_units(units,count)
    assert list(WorkflowRows(source,len(units))) == units


def test_recorded_conversations_are_never_repeated_to_fill_target(tmp_path):
    units = [{'id':'recorded','kind':'conversation','messages':[{'role':'user','content':'context'}],'status':'ready'}]
    rows = prepare_generation_rows(tmp_path/'generated.jsonl',units,50000,noop)
    assert len(rows) == 1 and list(rows) == units


def test_fifty_thousand_checkpoint_rows_and_candidates_keep_bounded_memory(tmp_path):
    def source():
        for index in range(50000):
            yield {'id':str(index),'kind':'document','text':'Recorded source content. '*50,'status':'ready'}
    tracemalloc.start()
    try:
        rows = row_checkpoint(tmp_path/'source.json',source,noop)
        candidates = prepare_generation_rows(tmp_path/'candidates.jsonl',rows.ready(50000,50000),50000,noop)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert len(rows) == len(candidates) == 50000
    assert peak < 3*1024*1024
    assert candidates[0]['id'] == '0' and candidates[-1]['id'] == '49999'
