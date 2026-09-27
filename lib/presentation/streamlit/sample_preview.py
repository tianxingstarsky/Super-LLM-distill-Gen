"""Page long conversations without splitting a user turn or a tool exchange."""
from __future__ import annotations

import streamlit as st

from lib.presentation.streamlit.artifact_preview import (
    _messages, render_training_sample,
)
from lib.domain.conversation_structure import message_windows, tool_result_user
from lib.presentation.streamlit.i18n import translate


def _select_section(widgets, key, page):
    widgets.session_state[key] = page


def render_sample_preview(target, row, *, key, wrapper_class=None, widgets=st):
    messages = _messages(row.get('messages')) if isinstance(row,dict) else []
    windows = message_windows(target,messages) if target in {'sft','multiturn','agent','agent_negative'} else []
    start, end, step_offset = 0, len(messages), 0
    projected = row
    turn_offset = 0
    if len(windows) > 1:
        language = widgets.session_state.get('ui_language','zh')
        position_key = key + ':message-page'
        failure = row.get('failure_step') if target == 'agent_negative' else None
        default = next((i+1 for i,(a,b,_) in enumerate(windows)
                        if type(failure) is int and a <= failure < b),1)
        if position_key in widgets.session_state:
            widgets.session_state[position_key] = min(max(1,widgets.session_state[position_key] or default),len(windows))
        picker, previous, following = widgets.columns([2,1,1],vertical_alignment='bottom')
        with picker:
            page = widgets.number_input('对话片段',1,len(windows),
                                        value=None if position_key in widgets.session_state else default,
                                        key=position_key)
        page = int(page or default)
        previous.button('上一片段',disabled=page<=1,on_click=_select_section,
                        args=(widgets,position_key,page-1),key=position_key+':previous',width='stretch')
        following.button('下一片段',disabled=page>=len(windows),on_click=_select_section,
                         args=(widgets,position_key,page+1),key=position_key+':next',width='stretch')
        start,end,step_offset = windows[page-1]
        widgets.caption(translate('当前消息范围',language)+f': {start+1:,}–{end:,} / {len(messages):,}')
        widgets.caption('仅渲染当前片段；完整记录保持不变。工具调用与对应返回不会拆开。')
        projected = {**row,'messages':messages[start:end]}
        if type(failure) is int:
            projected['failure_step'] = failure-start
        turn_offset = sum(message.get('role') == 'user' and not tool_result_user(message)
                          for message in messages[:start])
    markup = render_training_sample(target,projected,message_offset=start,
                                    turn_offset=turn_offset,step_offset=step_offset)
    if wrapper_class:
        markup = '<div class="'+wrapper_class+'">'+markup+'</div>'
    widgets.html(markup)
