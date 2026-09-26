"""Existing-file quality gates share strict recorded tool-error aliases."""
import copy
import pytest
from lib.domain.release_quality import report, sample_hash
from lib.application.release_service import ReleaseApplication


def sample(i,flags):
    return {'id':str(i),'messages':[{'role':'user','content':str(i)},
        {'role':'tool','content':'observed result',**flags},
        {'role':'assistant','content':'final answer'}]}


@pytest.mark.parametrize('flags,code',[
    ({'isError':True},'unresolved_tool_error'),
    ({'is_error':True},'unresolved_tool_error'),
    ({'isError':True,'is_error':True},'unresolved_tool_error'),
    ({'is_error':'false'},'invalid_tool_error_flag'),
    ({'isError':0},'invalid_tool_error_flag'),
    ({'is_error':None},'invalid_tool_error_flag'),
    ({'isError':False,'is_error':True},'invalid_tool_error_flag'),
])
def test_error_aliases_block_even_fully_reviewed_dataset(flags,code,tmp_path):
    rows=[sample(i,flags) for i in range(10)]
    original=copy.deepcopy(rows)
    votes=[{'sample_id':row['id'],'sample_hash':sample_hash(row),'decision':'keep'} for row in rows]
    quality=report(iter(rows),votes)
    assert quality['review_coverage']==1 and quality['review']['release']
    assert all(issue['code']==code for issue in quality['issues'])
    assert quality['issue_count']==10 and not quality['ready_for_bulk']
    class Driver:
        def write_release(self,*args,**kwargs): pytest.fail('blocked dataset reached storage')
    with pytest.raises(ValueError,match='structural_errors'):
        ReleaseApplication(Driver()).export_release(rows,'chat',tmp_path,votes,bulk=True)
    assert rows==original and list(tmp_path.iterdir())==[]


@pytest.mark.parametrize('flags',[{}, {'isError':False}, {'is_error':False}, {'isError':False,'is_error':False}])
def test_false_aliases_do_not_create_error_issues(flags):
    quality=report([sample(1,flags)])
    assert quality['issue_count']==0
