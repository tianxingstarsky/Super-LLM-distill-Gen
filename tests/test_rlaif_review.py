"""RLAIF review preserves AI feedback and publishes its native training schema."""
import copy
import hashlib
import json
from pathlib import Path
import time
import zipfile
import pytest
from lib.bootstrap.preference_reviews import preference_review_application
from lib.domain.preference_review import review_record
from lib.domain.review_audit import validate_review_event
from lib.domain.workflow_targets import training_record
from test_rlaif_targets import pair


def fixture_app(root,count=3):
    run_id='c'*32
    run=root/'workflows'/run_id
    artifacts=run/'artifacts';artifacts.mkdir(parents=True)
    rows=[]
    for i in range(count):
        row=pair()
        row.update(id=f'rlaif-{i}',status='eligible',source_id=f'source-{i}')
        row['prompt'][0]['content']=f'Question {i}: 2+2=?'
        rows.append(row)
    (artifacts/'rlaif.jsonl').write_text(''.join(json.dumps(training_record('rlaif',row))+'\n' for row in rows),encoding='utf-8')
    (artifacts/'rlaif.records.json').write_text(json.dumps(rows),encoding='utf-8')
    refresh_manifest(artifacts)
    (run/'state.json').write_text(json.dumps({'id':run_id,'name':'RLAIF fixture','status':'completed','targets':['rlaif'],
        'created_at':'2026-09-27T00:00:00Z'}),encoding='utf-8')
    return preference_review_application(root,target='rlaif'),run_id,rows


def refresh_manifest(artifacts):
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in artifacts.iterdir() if p.name!='manifest.json'}
    (artifacts/'manifest.json').write_text(json.dumps({'status':'complete','sha256':hashes}),encoding='utf-8')


def test_rlaif_review_to_real_worker_release_keeps_native_feedback(tmp_path):
    app,run_id,rows=fixture_app(tmp_path)
    source=(tmp_path/'workflows'/run_id/'artifacts'/'rlaif.jsonl').read_bytes()
    queue=app.queue(run_id)
    assert queue['total']==3 and queue['counts']['pending']==3
    for i,item in enumerate(queue['items']):
        assert item['pair']==rows[i]
        app.decide(run_id,item['pair_id'],expected_hash=item['pair_id'],decision='approved' if i<2 else 'rejected',reviewer='test')
    job=app.start_release(run_id)
    assert app.start_release(run_id)['id']==job['id']
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        result=app.release_job(run_id)
        if result['status'] not in {'queued','running'}: break
        time.sleep(.05)
    assert result['status']=='completed',result
    release=result['result']
    with app.release_archive(run_id,release['id'],release['sha256']) as handle,zipfile.ZipFile(handle) as archive:
        records=[json.loads(line) for line in archive.read('rlaif.jsonl').decode().splitlines()]
        assert records==[training_record('rlaif',row) for row in rows[:2]]
        assert 'responses' in records[0] and 'chosen' not in records[0]
        audit=json.loads(archive.read('review.json'))
        assert 'Arithmetic is correct.' in json.dumps(audit)
    assert (tmp_path/'workflows'/run_id/'artifacts'/'rlaif.jsonl').read_bytes()==source


def test_rlaif_answer_revision_requires_new_feedback():
    source=pair()
    with pytest.raises(ValueError,match='rlaif_revision_requires_new_feedback'):
        review_record(source,target='rlaif',decision='approved',reviewer='test',reviewed_at='now',chosen='new answer')
    record=review_record(source,target='rlaif',decision='approved',reviewer='test',reviewed_at='now')
    forged=copy.deepcopy(record);forged['candidate']['chosen'][0]['content']='new answer'
    with pytest.raises(ValueError,match='rlaif_revision_requires_new_feedback'):
        validate_review_event('rlaif',source,forged)


def test_native_feedback_must_match_verified_quality_record(tmp_path):
    app,run_id,rows=fixture_app(tmp_path)
    artifacts=tmp_path/'workflows'/run_id/'artifacts'
    path=artifacts/'rlaif.jsonl'
    native=[training_record('rlaif',row) for row in rows]
    native[0]['responses'][0]['feedback']='unrelated feedback'
    path.write_text(''.join(json.dumps(row)+'\n' for row in native),encoding='utf-8')
    refresh_manifest(artifacts)
    with pytest.raises(ValueError,match='rlaif_feedback_artifact_mismatch'):
        app.queue(run_id)

def test_rlaif_review_ui_locks_scored_responses(tmp_path):
    from streamlit.testing.v1 import AppTest
    app,run_id,rows=fixture_app(tmp_path)
    script=f'''
from lib.bootstrap.preference_reviews import preference_review_application
from lib.presentation.streamlit.preference_review_page import render_preference_review
render_preference_review(preference_review_application({str(tmp_path)!r},target="rlaif"))
'''
    ui=AppTest.from_string(script,default_timeout=15).run()
    assert not ui.exception
    assert [item.value for item in ui.metric if item.label in {'更优回答评分','对照回答评分'}]==['5','1']
    assert next(item for item in ui.toggle if item.label=='编辑两个回答').disabled
    assert next(item for item in ui.button if item.label=='交换偏好后通过').disabled
    assert next(item for item in ui.button if item.label=='通过并保留 AI 反馈')


def test_rlaif_native_and_evidence_counts_must_match(tmp_path):
    app,run_id,rows=fixture_app(tmp_path)
    artifacts=tmp_path/'workflows'/run_id/'artifacts'
    path=artifacts/'rlaif.jsonl'
    path.write_text(json.dumps(training_record('rlaif',rows[0]))+'\n',encoding='utf-8')
    refresh_manifest(artifacts)
    with pytest.raises(ValueError,match='rlaif_feedback_artifact_count_mismatch'):
        app.queue(run_id)
