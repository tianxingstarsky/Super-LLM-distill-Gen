"""Page long conversations without splitting a user turn or a tool exchange."""
from __future__ import annotations

from itertools import islice

import streamlit as st

from lib.presentation.streamlit.artifact_preview import (
    _messages, render_training_sample,
)
from lib.domain.conversation_structure import message_windows, tool_result_user
from lib.presentation.streamlit.i18n import translate


def _select_section(widgets, key, page):
    widgets.session_state[key] = page


def _section_shortcuts(widgets, position_key, page, pages, failure_page=None):
    columns = widgets.columns(3 if failure_page is not None else 2, gap='small')
    columns[0].button('首个片段', disabled=page == 1, on_click=_select_section,
                      args=(widgets, position_key, 1), key=position_key+':first', width='stretch')
    columns[1].button('最后片段', disabled=page == pages, on_click=_select_section,
                      args=(widgets, position_key, pages), key=position_key+':last', width='stretch')
    if failure_page is not None:
        columns[2].button('定位失败步骤', disabled=page == failure_page, on_click=_select_section,
                          args=(widgets, position_key, failure_page), key=position_key+':failure', width='stretch')


def render_sample_preview(target, row, *, key, wrapper_class=None, widgets=st, expand_trace=False):
    messages = _messages(row.get('messages')) if isinstance(row,dict) else []
    windows = message_windows(target,messages) if target in {'sft','multiturn','agent','agent_negative'} else []
    start, end, step_offset = 0, len(messages), 0
    projected = row
    turn_offset = 0
    response_offset = 0
    responses = row.get('responses') if isinstance(row,dict) and target == 'rlaif' else None
    if isinstance(responses,list) and len(responses) > 4:
        language = widgets.session_state.get('ui_language','zh')
        position_key = key + ':response-page'
        pages = (len(responses)+3)//4
        if position_key in widgets.session_state:
            widgets.session_state[position_key] = min(max(1,widgets.session_state[position_key] or 1),pages)
        picker, previous, following = widgets.columns([2,1,1],vertical_alignment='bottom')
        with picker:
            page = widgets.number_input('候选回答片段',1,pages,
                                        value=None if position_key in widgets.session_state else 1,key=position_key)
        page = int(page or 1)
        previous.button('上一片段',disabled=page<=1,on_click=_select_section,
                        args=(widgets,position_key,page-1),key=position_key+':previous',width='stretch')
        following.button('下一片段',disabled=page>=pages,on_click=_select_section,
                         args=(widgets,position_key,page+1),key=position_key+':next',width='stretch')
        _section_shortcuts(widgets, position_key, page, pages)
        response_offset = (page-1)*4
        end_response = min(response_offset+4,len(responses))
        widgets.caption(translate('当前候选范围',language)+f': {response_offset+1:,}–{end_response:,} / {len(responses):,}')
        widgets.caption('每页最多展示四个候选，原始排序、评分和完整记录保持不变。')
        projected = {**row,'responses':responses[response_offset:end_response]}
    if len(windows) > 1:
        language = widgets.session_state.get('ui_language','zh')
        position_key = key + ':message-page'
        failure = row.get('failure_step') if target == 'agent_negative' else None
        failure_page = next((i+1 for i,(a,b,_) in enumerate(windows)
                             if type(failure) is int and a <= failure < b), None)
        default = failure_page or 1
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
        _section_shortcuts(widgets, position_key, page, len(windows), failure_page)
        start,end,step_offset = windows[page-1]
        widgets.caption(translate('当前消息范围',language)+f': {start+1:,}–{end:,} / {len(messages):,}')
        widgets.caption('仅渲染当前片段；完整记录保持不变。工具调用与对应返回不会拆开。')
        projected = {**row,'messages':messages[start:end]}
        if type(failure) is int:
            projected['failure_step'] = failure-start
        turn_offset = sum(message.get('role') == 'user' and not tool_result_user(message)
                          for message in islice(messages, start))
    markup = render_training_sample(target,projected,message_offset=start,
                                    turn_offset=turn_offset,step_offset=step_offset,response_offset=response_offset,
                                    expand_trace=expand_trace)
    if wrapper_class:
        markup = '<div class="'+wrapper_class+'">'+markup+'</div>'
    widgets.html(markup)
