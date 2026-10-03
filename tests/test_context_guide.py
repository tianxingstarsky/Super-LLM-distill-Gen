"""Page help stays with the task and uses the active route."""
from __future__ import annotations

from streamlit.testing.v1 import AppTest


SCRIPT = '''
import streamlit as st
from lib.presentation.streamlit.context_guide import render_context_guide
from lib.presentation.streamlit.i18n import install_streamlit_localization

st.session_state['ui_language'] = 'en'
install_streamlit_localization()

def navigate(page):
    st.session_state['nav'] = page

render_context_guide(st.session_state.get('current_page', '总览'), navigate)
'''


def test_overview_help_opens_workflow_without_a_separate_guide_route():
    ui = AppTest.from_string(SCRIPT).run()
    assert not ui.exception
    assert "Guide" not in [button.label for button in ui.button]
    ui.button(key="context-guide-start:总览").click().run()
    assert ui.session_state["context-guide-step:总览"] == 0
    assert "home-source-panel" in " ".join(item.proto.body for item in ui.get("html"))
    ui.button(key="context-guide-next:总览").click().run()
    assert ui.session_state["context-guide-step:总览"] == 1
    ui.button(key="context-guide-action:总览").click().run()
    assert not ui.exception
    assert ui.session_state["nav"] == "自动工作流"


def test_workflow_help_describes_node_models_in_place():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state["current_page"] = "自动工作流"
    ui.run()
    assert not ui.exception
    content = " ".join(item.value for item in [*ui.markdown, *ui.caption])
    assert "Set models on nodes" in content
    assert "Select a model node" in content
    assert all(button.key != "context-guide-action:自动工作流" for button in ui.button)
