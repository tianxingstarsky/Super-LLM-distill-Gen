"""Literal hypothetical scope is distinct from invented support conditions.

These check provenance and conservative release, not model entailment accuracy.
"""
from copy import deepcopy
from hashlib import sha256

import pytest

from lib.domain.adversarial_claim_review import validate_adversarial_claim_review
from lib.prompts import get
from tests.test_condition_review_protocol import requests, payload, premise


@pytest.mark.parametrize('sentence,antecedent', [
    ('Boundary check: if the first key is 001, its leading zeros remain.', 'if the first key is 001'),
    ('For a header-only input, the loop writes no data rows.', 'For a header-only input'),
])
def test_explicit_example_antecedent_is_bound_to_candidate_without_certifying_it_as_observed(sentence, antecedent):
    body=payload();body['messages'][-1]['content']=sentence
    _,_,_,_,review_requests,raws=requests(body=body)
    request,raw=review_requests[-1],raws[-1]
    item=raw['claim_checks'][0]
    item['premises']=[premise('candidate','task_condition',antecedent,unit_id=item['unit_id'])]
    item['basis']={'kind':'logic','evidence':[],
        'derivation':'The conditional case concerns only the stated input; a string key keeps its characters and an empty record iterator has no data iteration.'}
    checked=validate_adversarial_claim_review(raw,request)
    assert checked['accepted'] and not checked['condition_guard']['needs_review']
    assert checked['condition_guard']['semantic_verification']=='provenance_only_not_entailment'
    assert checked['raw_response']==raw

    invented=deepcopy(raw)
    invented['claim_checks'][0]['premises']=[premise('assumption','task_condition',antecedent)]
    stopped=validate_adversarial_claim_review(invented,request)
    assert not stopped['accepted'] and stopped['condition_guard']['needs_review']
    assert stopped['raw_response']['verdict']['keep'] is True
    assert stopped['verdict']['keep'] is False


def test_previous_condition_prompts_are_byte_identical_after_scope_guidance_added():
    for name,version,digest in (
        ('workflow.review_score','1.21.0','6883c3d91c73e91c569341e5d22a979ebc83a36ffc1df5ad5db1404d85e0a7be'),
        ('workflow.review_reference','2.1.0','2eca85ffa83c66376b60125007ea4e7f2506b2a68ca26eecfbd3cc3be7a13293'),
    ):
        assert sha256(get(name,version).template.encode()).hexdigest()==digest
