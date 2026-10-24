"""New verified references trigger reassessment before buying a rewrite."""
from copy import deepcopy
from lib.contexts.review.application.review_candidate import ReviewCandidate
from tests.test_contrastive_preference import grade


class Port:
    run_id='child'
    authenticated_revision=True
    def __init__(self):self.events=[]
    def evidence(self,row):return {'source_kind':'synthetic','teacher_evidence':'Verified runtime evidence.'}
    def payload(self,target,row):return {key:deepcopy(row[key]) for key in ('question','reasoning','answer')}
    def repair(self,target,row,evidence,key,feedback,instruction):self.events.append('repair');return row,None
    def score(self,target,row,evidence,key,instruction):self.events.append('score');return grade()
    def record_revision_style(self,*args):pass
    def independent_cot_assessment(self,*args,**kwargs):return {}


def test_reassess_unchanged_body_with_new_evidence_and_keep_legacy_repair_order():
    row={'id':'parent','source_id':'source','question':'Does the CSV writer quote commas?',
         'reasoning':['The runtime observation confirms quoting.'],'answer':'Yes.','status':'quarantined'}
    config={'review_repair':{'mode':'auto','max_rounds':1,'reference_sources':True}}
    item={'target':'cot','record':row,'revision_context':{'candidate_id':'parent'}}
    old=Port();old_result=ReviewCandidate(old,config).execute(item)[0]
    assert old.events==['repair','score'] and old_result['review_repair']['attempts']==1
    current=Port();current_result=ReviewCandidate(current,{**config,'review_reference_reassessment_version':1}).execute(item)[0]
    assert current.events==['score'] and current_result['review_repair']['attempts']==0
    assert current_result['reasoning']==row['reasoning'] and current_result['answer']==row['answer']
    assert current_result['review_repair']['status']=='accepted'
