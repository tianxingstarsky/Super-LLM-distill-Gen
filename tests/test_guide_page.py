"""The guide must send users to the right workspace workflow and pages."""
from __future__ import annotations

from streamlit.testing.v1 import AppTest


SCRIPT = '''
import streamlit as st
from lib.presentation.streamlit.guide_page import render_guide
from lib.presentation.streamlit.i18n import install_streamlit_localization

st.session_state['ui_language'] = 'en'
install_streamlit_localization()

def navigate(page):
    st.session_state['nav'] = page

render_guide('guide-fixture', '客户资料 <A>',
             has_source_files=False, navigate=navigate)
'''
SCRIPT = SCRIPT.encode("ascii", "backslashreplace").decode("ascii")


def test_guide_starts_an_open_brief_in_the_current_workspace():
    ui = AppTest.from_string(SCRIPT).run()
    assert not ui.exception
    assert ui.segmented_control(key="guide-source:guide-fixture").value == "开放需求"
    html = "".join(item.proto.body for item in ui.get("html"))
    assert "客户资料 &lt;A&gt;" in html
    assert "No source files yet" in html
    assert "Choose models in workflow nodes" in html

    ui.button(key="guide-start").click().run()
    assert not ui.exception
    assert ui.session_state["workflow-source-mode:guide-fixture"] == "开放需求"
    assert ui.session_state["nav"] == "自动工作流"


def test_guide_source_switch_and_direct_links_do_not_change_other_workspaces():
    ui = AppTest.from_string(SCRIPT).run()
    ui.segmented_control(key="guide-source:guide-fixture").set_value("Agent 上下文").run()
    ui.button(key="guide-start").click().run()
    assert ui.session_state["workflow-source-mode:guide-fixture"] == "Agent 上下文"
    assert "workflow-source-mode:other" not in ui.session_state

    ui.button(key="guide-services").click().run()
    assert ui.session_state["nav"] == "模型与密钥"
    ui.button(key="guide-nodes").click().run()
    assert ui.session_state["nav"] == "自动工作流"
    ui.button(key="guide-open-人工审核").click().run()
    assert ui.session_state["nav"] == "人工审核"
    ui.button(key="guide-settings").click().run()
    assert ui.session_state["nav"] == "系统设置"
