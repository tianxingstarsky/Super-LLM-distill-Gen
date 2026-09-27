"""Large open planning retains only a batch and its recent context."""
import json
import tracemalloc

import pytest

from lib.domain.open_task_plan import task_identity
from lib.infrastructure.planning_identities import PlanningIdentities
from lib.infrastructure.training_workflow import Workflow, create_run, digest
from lib.infrastructure.workflow_rows import WorkflowRows


def run_fixture(tmp_path,count):
    output=tmp_path/'output'
    rid=create_run(output,brief='Generate equipment maintenance tasks',targets=['sft'],
                   sample_count=count,max_units=100000)
    return Workflow(output,rid,tmp_path)


def test_fifty_thousand_planned_tasks_use_bounded_rows_and_recent_context(tmp_path):
    run=run_fixture(tmp_path,50000)
    calls=0
    def ask(key,role,prompt_id,request):
        nonlocal calls
        assert role=='generation' and prompt_id=='workflow.plan'
        offset=request['offset']
        assert request['previous_tasks']==[f'Maintenance scenario {i}: inspect the device before servicing.'
                                           for i in range(max(0,offset-10),offset)]
        assert request['count']==50
        calls+=1
        return {'tasks':[f'Maintenance scenario {i}: inspect the device before servicing.'
                         for i in range(offset,offset+request['count'])]}
    run.ask=ask
    tracemalloc.start()
    try:
        rows=run.plan()
        peak=tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert isinstance(rows,WorkflowRows) and len(rows)==50000 and calls==1000
    assert peak<3*1024*1024
    assert rows[0]['location']==1 and rows[-1]['location']==50000
    assert rows[-1]['text']=='Maintenance scenario 49999: inspect the device before servicing.'
    run.ask=lambda *args:pytest.fail('complete planning checkpoint must not call the model again')
    assert len(run.plan())==50000
    assert run.state['stages']['ingest']['cached']==50000
    assert run.state['stages']['ingest']['batches_done']==1000


def test_planning_identity_store_is_exact_and_resets_each_attempt(tmp_path):
    path=tmp_path/'identities.sqlite3'
    store=PlanningIdentities(path)
    try:
        store.update([task_identity('Cafe\u0301\n task'),task_identity('CASE task')])
        assert task_identity('Café   task') in store
        assert task_identity('case task') not in store
    finally:
        store.close()
    restored=PlanningIdentities(path)
    try:
        assert task_identity('Café task') not in restored
    finally:
        restored.close()


def test_changed_complete_plan_is_rejected_without_model_calls(tmp_path):
    run=run_fixture(tmp_path,1)
    run.ask=lambda *args:{'tasks':['Inspect equipment before starting maintenance.']}
    run.plan()
    path=run.path/'checkpoints'/'ingest'/f"{digest('planned_tasks')}.jsonl"
    path.write_text('{"text":"changed"}\n',encoding='utf-8')
    run.ask=lambda *args:pytest.fail('must reject changed plan')
    with pytest.raises(ValueError,match='checkpoint_integrity_error'):
        run.plan()


def test_legacy_complete_plan_keeps_original_identity_and_data(tmp_path):
    run=run_fixture(tmp_path,1)
    rows=[{'id':'saved-legacy','kind':'brief','text':'Original task','status':'ready'}]
    path=run.path/'checkpoints'/'ingest'/f"{digest('planned_tasks')}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'data':rows,'sha256':digest(rows)}),encoding='utf-8')
    run.ask=lambda *args:pytest.fail('legacy complete plan must replay')
    assert run.plan()==rows


def test_interrupted_planning_ui_shows_real_task_and_batch_counts(tmp_path):
    from streamlit.testing.v1 import AppTest
    run=run_fixture(tmp_path,125)
    def ask(key,role,prompt_id,request):
        if request['offset']:
            raise RuntimeError('offline interruption')
        return {'tasks':[f'Inspect device scenario {index} before maintenance.' for index in range(50)]}
    run.ask=ask
    state=run.execute()
    assert state['status']=='failed'
    assert not any(event['kind']=='stage_completed' and event.get('stage')=='ingest' for event in state['events'])
    script=f'''
import streamlit as st
from pathlib import Path
from lib.bootstrap.workflows import workflow_application
from lib.presentation.streamlit.workflow_page import render_run
from lib.presentation.streamlit.i18n import install_streamlit_localization
st.session_state['ui_language']='en'
st.session_state['ws']='fixture'
install_streamlit_localization()
render_run(workflow_application(Path({str(tmp_path)!r}),Path({str(tmp_path/'output')!r})),{run.state['id']!r},lambda *args:None,embedded=True)
'''
    ui=AppTest.from_string(script,default_timeout=15).run()
    assert not ui.exception
    assert any(item.value=='Batch 1 / 3' for item in ui.caption)
    rendered=''.join(item.proto.body for item in ui.get('html'))
    assert '<b>50</b><span>Planned tasks</span>' in rendered
    assert '<b>75</b><span>Tasks to plan</span>' in rendered
