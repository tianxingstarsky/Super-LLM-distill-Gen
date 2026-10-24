"""Actual Flash paraphrase failure, exact context binding and bounded feedback."""
from copy import deepcopy
import pytest
from lib.contexts.review.domain.context_quote_feedback import context_quote_feedback
from lib.domain.claim_review import _reference


def request():
    return {'messages':[{'role':'user','content':'两个文件都是带 UTF-8 BOM 的 CSV，列是 key,note；key 像 001 这样要原样保留；'}],
            'scope':{'message_index':1}}


@pytest.mark.parametrize('quote',['key 像 001 要原样保留','两个文件都是带 UTF-8 BOM 的 CSV，列为 key,note'])
def test_real_paraphrases_remain_invalid_and_feedback_gives_only_actual_context(quote):
    ref={'quote':quote,'message_index':0,'field':'content'}
    response={'claim_checks':[{'basis':{'evidence':[deepcopy(ref)]}}]}
    original=deepcopy(response)
    with pytest.raises(ValueError,match='fabricated_claim_review_evidence_quote'):
        _reference(ref,request(),'context')
    failure,=context_quote_feedback(response,request())['invalid_context_quotations']
    assert failure['code']=='quote_not_in_context'
    assert failure['actual_context_verbatim']==request()['messages'][0]['content']
    assert failure['response_path']==['claim_checks',0,'basis','evidence',0,'quote']
    assert response==original
    ref['quote']='key 像 001 这样要原样保留'
    _reference(ref,request(),'context')
    assert context_quote_feedback({'condition_ledger':[{'premises':[ref]}]},request())=={}


def test_wrong_context_location_and_unbounded_source_are_not_guessed_or_truncated():
    req=request(); req['messages'].append({'role':'assistant','content':'x'*4001});req['scope']['message_index']=2
    ref={'quote':'要原样保留','message_index':1,'field':'content'}
    failure,=context_quote_feedback({'condition_ledger':[{'premises':[ref]}]},req)['invalid_context_quotations']
    assert failure['exact_quote_context_locations']==[{'message_index':0,'field':'content'}]
    assert 'actual_context_verbatim' not in failure
    assert 'actual_context_sha256' in failure
    assert context_quote_feedback({'untrusted arbitrary key':[ref]},req)=={}
