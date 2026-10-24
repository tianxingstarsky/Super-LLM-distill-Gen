"""The real batch pipeline uses one reviewed pair for three training formats."""
from copy import deepcopy
import json
from pathlib import Path
from lib.infrastructure import training_workflow as engine
from tests.test_workflow_production import model_request_data
from tests.review_protocol_fixtures import source_condition_response

SOURCE='Inspect the equipment only after turning off the power. Record the result after inspection.'
POSITIVE='Turn off the power before inspection.'
NEGATIVE='Sourdough needs regular feeding.'


def grade(keep):
    score=5 if keep else 1
    return {'keep':keep,'grounded':keep,'reasoning_valid':keep,'correctness':score,
        'scores':{key:score for key in ('correctness','reasoning','grounding','instruction','safety')},
        'reason':'The actual response answers the task.' if keep else 'The response discusses sourdough instead of the equipment question.'}


class ContrastClient:
    model='offline-contrast-contract'
    def __init__(self):self.calls=[];self.usage={'calls':0}
    def chat(self,messages,**options):
        request=model_request_data(messages);self.calls.append(deepcopy(request));self.usage['calls']+=1
        if 'reference_response' in request:
            return json.dumps({'answer':NEGATIVE,'reasoning':'Feeding sustains yeast.'})
        if 'contrastive_design' in request:
            assert 'rationale_supports_answer' in messages[0]['content']
            return json.dumps({'same_task':False,'opposite':False,'off_topic':True,'rationale_supports_answer':True,
                'chosen_claim':POSITIVE,'rejected_claim':NEGATIVE,'rationale_quote':'Feeding sustains yeast.',
                'reason':'The negative discusses food fermentation, not equipment inspection.'})
        if request.get('claim_review_protocol_version')==4:
            negative=NEGATIVE in json.dumps(request['target_fields'])
            return json.dumps(source_condition_response(messages,request,grade(not negative),quote=SOURCE,
                refuted_text=NEGATIVE,derivation='Check whether the actual response preserves the original power-off prerequisite.'))
        raise AssertionError('Unexpected request: '+str(list(request)))


def test_explicit_design_traverses_real_batch_review_and_native_three_format_exports(tmp_path,monkeypatch):
    source=tmp_path/'source.txt';source.write_text(SOURCE,encoding='utf-8')
    output=tmp_path/'out'
    run_id=engine.create_run(output,sources=[source],targets=['dpo','orpo','rlaif'],
        cpt_processing={'mode':'native'},qa_director={'enabled':False},max_units=1,tasks=1,
        preference_design={},review_repair={'mode':'auto','max_rounds':1})
    def sft(self,unit):
        return [{'id':unit['id'],'source_id':unit['source_id'],'status':'eligible','source_context':unit,
            'messages':[{'role':'user','content':'When should I turn the power off?'},
                {'role':'assistant','content':POSITIVE,'reasoning_content':
                    'Inspection must follow shutdown. If inspection starts while power is still on, the prerequisite is violated. '
                    'Switching power off afterward cannot make the earlier inspection safe. The supported order is shutdown, inspection, then recording.'}],
            'quotes':[SOURCE],'evidence_level':'source_and_model_assessed'}]
    monkeypatch.setattr(engine.Workflow,'sft',sft)
    client=ContrastClient()
    state=engine.Workflow(output,run_id,Path(__file__).resolve().parents[1],generator=client,judge=client).execute()
    assert state['status']=='completed',state.get('error')
    run=engine.run_path(output,run_id); manifest=engine.verify_artifacts(run)
    assert manifest['counts']=={'dpo':1,'orpo':1,'rlaif':1}
    rows=[engine.read_json(run/'artifacts'/f'{target}.records.json')[0] for target in ('dpo','orpo','rlaif')]
    assert all(row['chosen']==rows[0]['chosen'] and row['rejected']==rows[0]['rejected'] for row in rows)
    assert len({row['id'] for row in rows})==1
    assert len([request for request in client.calls if 'contrastive_design' in request and 'candidate' in request])==1
    assert all(row['preference']['chosen']['correctness']==5 and row['preference']['rejected']['correctness']==1 for row in rows)
    assert all(row['rejected'][0]['reasoning_content']=='Feeding sustains yeast.' for row in rows)
