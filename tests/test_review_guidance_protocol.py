"""Real reviewer notes after user, one native response and unchanged historical drafts."""
from copy import deepcopy
import json

import httpx
import pytest

from test_native_feedback_continuation import Port, session, original, findings, response, revised
from test_model_tool_calls import sdk_client, reply
from lib.contexts.workflows.application.continue_native_feedback import ContinueNativeFeedback
from lib.contexts.workflows.domain.native_feedback_provenance import authenticate_native_feedback_receipt
from lib.contexts.workflows.domain.review_guidance import authenticated_review_guidance_positions
from lib.contexts.workflows.domain.reasoning_checklist import checklist_digest
from lib.domain.workflow_node_prompts import snapshot_node_prompts
from lib.prompts import get, render


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def test_review_notes_are_system_after_real_user_and_exact_native_output_is_preserved():
    author, old, notes = session(), original(), findings()
    before = deepcopy((author, old, notes))
    port = Port([response(revised())])
    service = ContinueNativeFeedback(port, guidance_version=7)
    value, receipt = service.execute(['notes'], author, old, findings=notes)
    assert receipt['status'] == 'completed' and receipt['model_turns'] == 1
    request = port.calls[0]
    assert request['final'] is True and request['tools'] == author['tools']
    assert request['messages'][:-1] == author['messages']
    assert request['messages'][-2]['role'] == 'user'
    assert request['messages'][-1] == receipt['guidance_message']
    assert request['messages'][-1]['role'] == 'system'
    assert notes[0]['issue'] in request['messages'][-1]['content']
    assert old not in request['messages'] and receipt['original_assistant'] == old
    assert 'tool_feedback' not in receipt and value['reasoning'] == revised()['reasoning_content']
    assert receipt['semantic_status'] == 'not_assessed' and not receipt['independent_review']
    assert (author, old, notes) == before
    assert authenticate_native_feedback_receipt(receipt)['value'] == value
    assert service.execute(['notes'], author, old, findings=notes) == (value, receipt)
    assert len(port.calls) == 1
    row = {'question': request['messages'], 'reasoning':[value['reasoning']], 'answer':value['answer'],
        'native_generation':{'review_guidance':receipt,
            'content_sha256':checklist_digest([value['reasoning'],value['answer']])}}
    assert authenticated_review_guidance_positions(row) == (2,)
    damaged = deepcopy(receipt)
    damaged['guidance_message']['content'] += '\nAssume all uninspected pages agree.'
    with pytest.raises(ValueError, match='provenance_mismatch'):
        authenticate_native_feedback_receipt(damaged)


@pytest.mark.parametrize('change,issue', [
    ('false_positive','review_guidance_no_actionable_notes'),
    ('unbound','native_feedback_unbound_finding'),
])
def test_non_actionable_or_unbound_findings_do_not_purchase_a_request(change,issue):
    notes=findings()
    if change == 'false_positive': notes[0]['disposition']='false_positive'
    else: notes[0]['quote']='not present in the draft'
    port=Port([])
    value,receipt=ContinueNativeFeedback(port,guidance_version=7).execute(['invalid'],session(),original(),findings=notes)
    assert value is None and receipt['issue']==issue and not port.calls


def test_real_sdk_serializes_no_tool_regeneration_with_system_after_user():
    sent=[]
    def handle(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200,json=reply('chat',calls=False,thought='注意事项提醒我核对范围。',content='仅对所给节选作判断。'))
    client=sdk_client('chat',handle)
    messages=[{'role':'user','content':'这篇讲义没说吗？'},
        {'role':'system','content':'要注意：当前只提供了节选，未找到不等于全文没有。'}]
    try:
        value=client.tool_turn(messages,[],tool_choice='none',max_tokens=32768,retries=1)
        assert len(sent)==1 and sent[0]['messages']==messages
        assert 'tools' not in sent[0] and 'tool_choice' not in sent[0]
        assert value['reasoning_content']=='注意事项提醒我核对范围。'
        assert value['content']=='仅对所给节选作判断。'
        with pytest.raises(ValueError,match='invalid_model_tools'):
            client.tool_turn(messages,[],tool_choice='auto')
        assert len(sent)==1
    finally:
        client.client.close()


def test_new_review_notes_template_selection_preserves_legacy_snapshots():
    options=dict(recipe_version=42,claim_review_adversarial_protocol_version=1,
                 claim_review_condition_contract_version=1,claim_review_transport_version=1)
    old=snapshot_node_prompts(None,**options)
    new=snapshot_node_prompts(None,**options,native_feedback_guidance_version=7)
    assert old['review']['workflow.review_score']==render(get('workflow.review_score',version='1.22.0'))
    assert new['review']['workflow.review_score']==render(get('workflow.review_score',version='1.25.0'))
