"""Preview status distinguishes actual tool failure from invalid recorded flags."""
import copy
import pytest
from lib.render import render_message_sequence
from lib.review import _plain_messages
from lib.presentation.streamlit.dataset_browser_page import _message_tool_errors
from lib.presentation.streamlit.i18n import translate_markup


@pytest.mark.parametrize('flags,failed,invalid',[
    ({},False,False),({'is_error':False},False,False),({'isError':True},True,False),
    ({'is_error':True},True,False),({'is_error':'false'},False,True),
    ({'isError':0},False,True),({'is_error':None},False,True),
    ({'isError':True,'is_error':False},False,True),
])
@pytest.mark.parametrize('role',['tool','user','assistant'])
def test_preview_status_matches_strict_flags_without_mutating_source(flags,failed,invalid,role):
    result={'type':'tool_result','tool_use_id':'call-1','content':'工具错误标记无效 source text',**flags}
    message=({'role':'tool','content':result['content'],**flags} if role=='tool' else
             {'role':role,'content':[result]})
    source=copy.deepcopy(message)
    rendered=render_message_sequence([message])
    assert ('⚠ 执行失败' in rendered)==failed
    assert ('⚠ 工具错误标记无效' in rendered)==invalid
    assert _message_tool_errors(message)==(failed,invalid)
    assert message==source
    if role=='tool':
        summary=_plain_messages({'messages':[message]})
        assert ('工具结果❌' in summary)==failed
        assert ('工具结果⚠ 标记无效' in summary)==invalid


def test_invalid_status_localizes_but_raw_tool_body_is_preserved():
    rendered=render_message_sequence([{'role':'tool','content':'工具错误标记无效', 'is_error':'false'}])
    english=translate_markup(rendered,'en')
    assert '⚠ Invalid tool error flag' in english
    assert '工具错误标记无效' in english
