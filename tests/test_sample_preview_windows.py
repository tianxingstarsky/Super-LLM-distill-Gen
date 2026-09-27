"""Bound rendered conversations without breaking exchange boundaries."""
from streamlit.testing.v1 import AppTest
from lib.presentation.streamlit.sample_preview import message_windows


def test_tool_windows_preserve_parallel_results_and_unpaired_records():
    messages = []
    for i in range(9):
        messages.extend([
            {'role': 'assistant', 'tool_calls': [
                {'id': f'a{i}', 'function': {'name': 'read'}},
                {'id': f'b{i}', 'function': {'name': 'write'}}]},
            {'role': 'tool', 'tool_call_id': f'b{i}', 'content': 'B'},
            {'role': 'tool', 'tool_call_id': f'a{i}', 'content': 'A'}])
    messages.append({'role': 'tool', 'tool_call_id': 'unpaired', 'content': 'UNPAIRED'})
    assert message_windows('agent', messages) == [(0, 24, 0), (24, 28, 8)]


SCRIPT = """
import streamlit as st
from lib.presentation.streamlit.sample_preview import render_sample_preview
from lib.presentation.streamlit.i18n import install_streamlit_localization
st.session_state['ui_language']='en'
install_streamlit_localization()
messages=[]
for i in range(1000):
    messages.extend([{'role':'user','content':f'QUESTION_{i}'},{'role':'assistant','content':f'ANSWER_{i}'}])
render_sample_preview('multiturn',{'messages':messages},key='fixture')
"""


def test_long_dialogue_renders_only_selected_window_and_absolute_turns():
    ui = AppTest.from_string(SCRIPT).run()
    assert not ui.exception
    first = ''.join(item.proto.body for item in ui.get('html'))
    assert 'QUESTION_0<' in first and 'QUESTION_8<' not in first
    assert 'Turn 01' in first
    ui.number_input(key='fixture:message-page').set_value(125).run()
    assert not ui.exception
    last = ''.join(item.proto.body for item in ui.get('html'))
    assert 'QUESTION_999<' in last and 'QUESTION_0<' not in last
    assert 'Turn 993' in last and 'Turn 1000' in last
    assert 'Current message range: 1,985' in ui.caption[0].value
    assert len(last) < 60000
    assert ui.button(key='fixture:message-page:next').disabled
    ui.button(key='fixture:message-page:previous').click().run()
    assert ui.number_input(key='fixture:message-page').value == 124


def test_negative_preview_opens_failure_window_without_rewriting_source():
    script = """
import streamlit as st
from lib.presentation.streamlit.sample_preview import render_sample_preview
messages=[{'role':'user','content':'INPUT'}]
for i in range(20):
    messages.extend([{'role':'assistant','tool_calls':[{'id':str(i),'function':{'name':'read'}}]},
                     {'role':'tool','tool_call_id':str(i),'content':f'OBSERVED_{i}'}])
row={'messages':messages,'failure_step':30,'failure':'recorded_tool_error'}
render_sample_preview('agent_negative',row,key='negative')
st.json({'source_failure_step':row['failure_step'],'source_messages':len(row['messages'])})
"""
    ui = AppTest.from_string(script).run()
    assert not ui.exception
    assert ui.number_input(key='negative:message-page').value == 2
    markup = ''.join(item.proto.body for item in ui.get('html'))
    assert 'OBSERVED_14<' in markup and 'OBSERVED_0<' not in markup
    assert '第 31 条消息（索引 30）' in markup
    assert ' data-failed="true" data-verified=' in markup
    assert 'source_failure_step' in ui.json[0].value and '30' in ui.json[0].value


def test_anthropic_results_stay_in_their_turn():
    messages = [
        {'role': 'user', 'content': 'Q'},
        {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'x', 'name': 'read'}]},
        {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'x', 'content': 'R'}]},
        {'role': 'assistant', 'content': 'A'}, {'role': 'user', 'content': 'Q2'}]
    assert message_windows('multiturn', messages, size=1) == [(0, 4, 0), (4, 5, 1)]
