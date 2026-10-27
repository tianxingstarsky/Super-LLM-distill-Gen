"""Minimal contrasts for unit mistakes, scope coverage and frozen legacy behavior."""
from copy import deepcopy
import pytest
from lib.contexts.workflows.domain.reasoning_claims import claim_anchors, source_passages, verify_calculation, verify_claims
from lib.contexts.workflows.domain.reasoning_checklist import default_reasoning_checklist
from lib.contexts.workflows.application.check_reasoning import CheckReasoning
from lib.domain.workflow_node_prompts import snapshot_node_prompts, active_node_prompt_ids
from test_generation_node_use_cases import MemoryGenerationPort


def calculation(**kw):
    return {"operation":"difference", "baseline":"6.8", "value":"39.9", "reported":"33.1",
            "reported_quote":"33.1 分数点", "metric_kind":"score", "operand_scale":"native",
            "reported_unit":"score_point", **kw}


@pytest.mark.parametrize("changes,text,passed,issue", [
    ({}, "6.8 到 39.9，增加 33.1 分数点", True, None),
    ({"reported_quote":"33.1 个百分点","reported_unit":"percentage_point"}, "6.8 到 39.9，增加 33.1 个百分点", False, "calculation_unit_mismatch"),
    ({"reported_quote":"33.1 个百分点"}, "6.8 到 39.9，增加 33.1 个百分点", False, "calculation_reported_unit_substituted"),
    ({"operation":"ratio","reported":"4.87","reported_quote":"4.87 倍","reported_unit":"ratio"}, "39.9 是 6.8 的 4.87 倍", False, "calculation_value_mismatch"),
    ({"operation":"ratio","reported":"5.87","reported_quote":"5.87 倍","reported_unit":"ratio"}, "39.9 是 6.8 的 5.87 倍", True, None),
    ({"operation":"relative_change","reported":"486.8","reported_quote":"486.8%","reported_unit":"percent"}, "6.8 到 39.9，相对增幅486.8%", True, None),
    ({"operation":"percent_of","metric_kind":"quantity","value":"25","baseline":"175","reported":"14.3","reported_quote":"14.3%","reported_unit":"percent"}, "25项占175项的14.3%", True, None),
    ({"operation":"percent_of","metric_kind":"quantity","value":"25","baseline":"175","reported":"85.7","reported_quote":"85.7%","reported_unit":"percent"}, "25项占175项的85.7%", False, "calculation_value_mismatch"),
    ({"metric_kind":"proportion","operand_scale":"percent","reported_quote":"33.1个百分点","reported_unit":"percentage_point"}, "6.8%到39.9%，增加33.1个百分点", True, None),
    ({"baseline":"0.068","value":"0.399","metric_kind":"proportion","operand_scale":"fraction","reported_quote":"33.1个百分点","reported_unit":"percentage_point"}, "0.068到0.399，增加33.1个百分点", True, None),
    ({"baseline":"0","operation":"ratio"}, "0 到 39.9，33.1 分数点", False, "calculation_zero_baseline"),
    ({"value":"88"}, "6.8 到 39.9，增加 33.1 分数点", False, "calculation_uncited_operand"),
])
def test_typed_decimal_contrasts(changes, text, passed, issue):
    receipt=verify_calculation(calculation(**changes),text)
    assert receipt["passed"] is passed
    assert receipt.get("issue")==issue


def claims_for(body):
    return {"checks":[],"claims":[{"anchor_id":a["id"],"status":"supported",
        "evidence":[{"source":a["field"],"quote":a["quote"]}],"reason":"在明确条件内核查该句。",
        "counterexample":"只限定当前描述，不将可能性提升为必然；若换比较基线结论可改变。",
        "calculations":[],"no_calculation_reason":"这里是独立计数，不含差值或倍率断言。"}
        for a in claim_anchors(body)]}


def test_each_risky_clause_needs_its_own_result_and_exact_evidence():
    body={"reasoning":"有175项任务。所有任务必然成功。", "answer":"有175项任务。"}
    anchors=claim_anchors(body)
    assert len(anchors)==3 and len({a['id']for a in anchors})==3
    raw=claims_for(body)
    # Structural pass does not mean the broad assertion is true.
    assert verify_claims(anchors,[],body,raw)["passed"]
    del raw["claims"][1]
    assert verify_claims(anchors,[],body,raw)["issue"]=="claim_coverage_missing"
    raw=claims_for(body);raw['claims'][1]['counterexample']=''
    assert verify_claims(anchors,[],body,raw)["issue"]=="claim_missing_scope_test"
    raw=claims_for(body);raw['claims'][1]['status']='unsupported'
    assert not verify_claims(anchors,[],body,raw)["passed"]
    raw=claims_for(body);raw['claims'][0]['evidence'][0]['quote']='模型自称核查通过'
    assert verify_claims(anchors,[],body,raw)["issue"]=="claim_unbound_evidence"


def test_uncertain_cannot_become_accepted_and_feedback_is_not_source_evidence():
    body={"reasoning":"实际提升33.1个百分点。"};raw=claims_for(body)
    raw['claims'][0]['status']='uncertain'
    assert not verify_claims(claim_anchors(body),[],body,raw)['passed']
    passages=source_passages({'source':{'text':'原始定义。'},'feedback':{'text':'捏造结论'},'style':{'instruction':'风格'}})
    assert passages==[{'id':'source.text','text':'原始定义。'}]


def test_numeric_report_cannot_be_borrowed_from_other_source_instead_of_current_claim():
    receipt=verify_calculation(calculation(), '6.8 39.9 33.1 分数点', '增加33.1个百分点')
    assert receipt['issue']=='calculation_reported_quote'


def test_cited_number_formatting_preserves_signs_and_accepts_thousands_groups():
    from lib.contexts.workflows.domain.reasoning_claims import _number_present
    assert _number_present('52445', '有52,445项')
    assert _number_present('33.1', '增加+33.1点')
    assert _number_present('-6.8', '为−6.8分')
    assert not _number_present('6.8', '为−6.8分')


def test_v2_checker_keeps_independent_payload_blind_and_new_context_changes_key():
    config={'version':2,'rules':[{'id':'unit','requirement':'逐个检查数值。','allow_na':False}]}
    body={'reasoning':'有175项任务。','answer':'有175项任务。'}
    raw=claims_for(body);raw['checks']=[{'rule_id':'unit','status':'met','evidence':[{'field':'reasoning','quote':body['reasoning']}],'reason':'当前是单独计数，核对一致。'}]
    port=MemoryGenerationPort([raw,raw,raw,raw])
    checker=CheckReasoning(port,config)
    a=checker.execute(['sample'],{'source':{'text':'有175项任务。'}},body,phase='final')
    assert a['passed'] and a['version']==2 and len(a['claim_anchors'])==2
    assert port.calls[0][2]=='workflow.cot_claim_check' and port.calls[1][2]=='workflow.cot_claim_review'
    assert port.calls[0][3]==port.calls[1][3]
    checker.execute(['sample'],{'source':{'text':'修订后的175项任务。'}},body,phase='final')
    assert port.calls[0][0]!=port.calls[2][0]


def test_default_v2_templates_are_frozen_without_adding_them_to_v23():
    assert default_reasoning_checklist()['version']==2
    assert 'workflow.cot_claim_check' not in snapshot_node_prompts(None,recipe_version=23)['cot']
    assert 'workflow.cot_claim_check' in snapshot_node_prompts(None,recipe_version=24)['cot']


def test_long_audit_batches_keep_full_context_and_require_every_anchor():
    config={'version':2,'rules':[{'id':'unit','requirement':'逐个检查数值。','allow_na':False}]}
    body={'reasoning':''.join(f'这里有{i}项任务。' for i in range(13)), 'answer':'请保留全部。'}
    full=claims_for(body)
    check={'rule_id':'unit','status':'met','evidence':[{'field':'reasoning','quote':'这里有1项任务。'}], 'reason':'核查了完整正文。'}
    responses=[{'checks':[check] if i==0 else [],'claims':full['claims'][i:i+6]} for i in range(0,len(full['claims']),6)]
    port=MemoryGenerationPort(deepcopy(responses+responses))
    result=CheckReasoning(port,config).execute(['sample'],{'source':{'text':'完整资料。'}},body,phase='final')
    assert result['passed']
    assert len(port.calls)==6 and len(result['self_check']['claims'])==len(full['claims'])
    for i,call in enumerate(port.calls):
        assert call[3]['candidate']==body and call[3]['context']=={'source':{'text':'完整资料。'}}
        assert len(call[3]['claim_anchors'])<=6
        assert call[3]==port.calls[i%3][3]  # Reviewer never sees author verdict.
    broken=deepcopy(responses);broken[-1]['claims']=[]
    failed=CheckReasoning(MemoryGenerationPort(broken+[broken[-1]]),config).execute(['sample'],{},body,phase='final')
    assert not failed['passed'] and failed['issue']=='claim_coverage_missing'


def test_invalid_audit_evidence_gets_one_repair_without_reauthoring_or_judge_contamination():
    config={'version':2,'rules':[{'id':'unit','requirement':'核查数量。','allow_na':False}]}
    body={'reasoning':'有175项任务。','answer':'有175项任务。'}
    good=claims_for(body);good['checks']=[{'rule_id':'unit','status':'met','evidence':[{'field':'reasoning','quote':body['reasoning']}],'reason':'核对数量。'}]
    bad=deepcopy(good);bad['claims'][0]['evidence'][0]['quote']='改写过的175项任务'
    port=MemoryGenerationPort([bad,good,good])
    receipt=CheckReasoning(port,config).execute(['sample'],{},body,phase='final')
    assert receipt['passed'] and len(port.calls)==3
    assert port.calls[1][3]['candidate']==body
    assert port.calls[1][3]['assessment_repair']['previous_assessment']==bad
    assert 'assessment_repair' not in port.calls[2][3]
    assert receipt['self_check_format_repairs'][0]['original_assessment']==bad
    port=MemoryGenerationPort([bad,bad])
    failed=CheckReasoning(port,config).execute(['sample'],{},body,phase='final')
    assert not failed['passed'] and len(port.calls)==2


def test_real_negative_verdict_is_not_resampled_as_a_format_repair():
    config={'version':2,'rules':[{'id':'scope','requirement':'核查范围。','allow_na':False}]}
    body={'reasoning':'所有任务必然成功。'}
    bad=claims_for(body);bad['claims'][0]['status']='unsupported'
    bad['checks']=[{'rule_id':'scope','status':'unmet','evidence':[],'reason':'缺少支持必然性的条件。'}]
    port=MemoryGenerationPort([bad])
    assert not CheckReasoning(port,config).execute(['sample'],{},body,phase='prefix')['passed']
    assert len(port.calls)==1


def test_batch_semantic_failure_is_not_misreported_as_unexecuted_coverage():
    config={'version':2,'rules':[{'id':'scope','requirement':'核查范围。','allow_na':False}]}
    body={'reasoning':''.join(f'第{i}项必然成功。' for i in range(13))}
    raw=claims_for(body)
    raw['claims']=raw['claims'][:6]
    raw['claims'][2]['status']='unsupported'
    raw['checks']=[{'rule_id':'scope','status':'unmet','evidence':[],
                    'reason':'没有条件可以保证每项成功。'}]
    port=MemoryGenerationPort([raw])
    audit=CheckReasoning(port,config).execute(['sample'],{},body,phase='final')
    assert not audit['passed'] and audit['issue']=='claim_not_supported'
    assert audit['self_check_batch_failure']=={'index':0,'issue':'claim_not_supported'}
    assert audit['self_check_verification']['issue']=='claim_coverage_missing'
    assert audit['self_check_batches'][0]['verification']['items'][2]['status']=='unsupported'
    assert len(port.calls)==1  # No paid later batches or repeated negative vote.


def test_format_repair_reports_coverage_and_bad_citations_together():
    config={'version':2,'rules':[{'id':'scope','requirement':'核查范围。','allow_na':False}]}
    body={'reasoning':'有175项任务。还有25个实例。'}
    good=claims_for(body)
    good['checks']=[{'rule_id':'scope','status':'met','evidence':[{'field':'reasoning','quote':'有175项任务。'}], 'reason':'核对范围。'}]
    bad=deepcopy(good);bad['checks'][0]['evidence'][0]['quote']='不在正文中的引文'
    bad['claims'].pop()
    port=MemoryGenerationPort([bad,good,good])
    receipt=CheckReasoning(port,config).execute(['sample'],{},body,phase='final')
    assert receipt['passed'] and len(port.calls)==3
    repair=port.calls[1][3]['assessment_repair']
    assert repair['validation_issue']=='checklist_evidence_not_in_candidate'
    details=repair['validation_details']
    assert details['missing_anchor_ids']==[claim_anchors(body)[-1]['id']]
    assert details['rule_errors'][0]['issue']=='checklist_evidence_not_in_candidate'
    assert details['claim_errors'][0]['verification']['issue']=='claim_coverage_missing'
    assert 'assessment_format_repair_v2' in port.calls[1][0]
    assert port.calls[1][3]['candidate']==body and 'assessment_repair' not in port.calls[2][3]


def test_citation_omission_can_be_repaired_only_with_existing_verifiable_operands():
    from lib.contexts.workflows.domain.reasoning_claims import assessment_contract_issue
    body={'reasoning':'25项占总量的14.3%。'}
    raw=claims_for(body)
    raw['claims'][0]['calculations']=[calculation(operation='percent_of',baseline='175',value='25',
        reported='14.3',reported_quote='14.3%',metric_kind='quantity',reported_unit='percent')]
    anchors=claim_anchors(body);passages=[{'id':'source.text','text':'共有175项任务。'}]
    assert assessment_contract_issue([],anchors,passages,body,raw)=='calculation_uncited_operand'
    # The original strict validator still refuses the missing evidence.
    assert not verify_claims(anchors,passages,body,raw)['passed']
    fixed=deepcopy(raw);fixed['claims'][0]['evidence'].append({'source':'source.text','quote':'共有175项任务。'})
    assert verify_claims(anchors,passages,body,fixed)['passed']
    assert assessment_contract_issue([],anchors,[],body,raw) is None
    wrong=deepcopy(raw);wrong['claims'][0]['calculations'][0]['baseline']='150'
    assert assessment_contract_issue([],anchors,passages,body,wrong) is None


def test_latest_claim_prompt_does_not_supply_paper_numeric_answers_and_legacy_is_frozen():
    from lib.prompts import get, render
    current=render(get('workflow.cot_claim_check'))
    assert 'percent_of' in current and 'assessment_repair' in current
    assert '39.9' not in current and '33.1' not in current
    assert '39.9' in render(get('workflow.cot_claim_check','1.1.0'))


def test_malformed_nested_labels_fail_validation_instead_of_crashing_the_run():
    body={'reasoning':'有175项任务。'};raw=claims_for(body)
    raw['claims'][0]['status']=['supported']
    assert verify_claims(claim_anchors(body),[],body,raw)['issue']=='claim_invalid_item'
    from lib.contexts.workflows.domain.reasoning_claims import assessment_contract_issue
    assert assessment_contract_issue([],claim_anchors(body),[],body,raw)=='claim_invalid_item'
    assert verify_calculation(calculation(operation=['difference']), '6.8 39.9 33.1 分数点')['issue']=='calculation_schema'


def test_checklist_draft_fields_roundtrip_without_accepting_unknown_fields():
    from lib.domain.creation_draft import validate_creation_draft
    from lib.presentation.streamlit.workflow_reuse import recipe_to_draft
    config=default_reasoning_checklist()
    values=recipe_to_draft({'node_generation':{'cot':{'enabled':True,'self_check':config}}},'Copy','default',[])['values']
    assert values['workflow-cot-checklist-enabled:default'] is True
    assert config['rules'][-1]['requirement'] in values['workflow-cot-checklist-text:default']
    assert validate_creation_draft(values)==values
    with pytest.raises(ValueError): validate_creation_draft({'workflow-cot-checklist-unknown:default':True})
    with pytest.raises(ValueError): validate_creation_draft({'workflow-cot-checklist-text:default':'x'*32001})
