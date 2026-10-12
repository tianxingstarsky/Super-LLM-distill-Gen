"""Changing one shared admission field must not depend on another widget."""
import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


SCRIPT = '''
import streamlit as st
from lib.presentation.streamlit.model_scheduling_controls import _save_scheduling, render_model_scheduling
st.session_state.setdefault("policy", {"max_concurrency": 4, "request_queue_timeout_seconds": 81})
class App:
    def get_model_scheduling(self, backend, model):
        if st.session_state.get("fail-read"):
            raise OSError("fixture storage unavailable")
        return {**st.session_state["policy"], "active": 0, "queued": 0}
    def save_model_scheduling(self, backend, model, **values):
        st.session_state["saved"] = values
        st.session_state["policy"] = values
app = App()
if st.session_state.get("callback"):
    _save_scheduling(app, "local", "model", "fixture", st.session_state["callback"])
else:
    st.selectbox("Node", ["trim", "sft"], key="node")
    render_model_scheduling(app, "local", "model", prefix=st.session_state["node"])
'''


@pytest.mark.parametrize("changed,key,value,expected", [
    ("limit", "fixture:limit", 12, {"max_concurrency": 12, "request_queue_timeout_seconds": 81}),
    ("wait", "fixture:wait", 124, {"max_concurrency": 4, "request_queue_timeout_seconds": 124}),
])
def test_callback_survives_missing_sibling_widget(changed, key, value, expected):
    ui = AppTest.from_string(SCRIPT)
    ui.session_state["callback"] = changed
    ui.session_state[key] = value
    ui.run()
    assert not ui.exception
    assert ui.session_state["saved"] == expected


def test_callback_does_not_restore_a_stale_sibling_policy():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state["callback"] = "limit"
    ui.session_state["fixture:limit"] = 7
    ui.session_state["fixture:wait"] = 2
    ui.run()
    assert not ui.exception
    assert ui.session_state["saved"] == {"max_concurrency": 7, "request_queue_timeout_seconds": 81}


def test_disposed_callback_has_no_write_or_error():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state["callback"] = "limit"
    ui.run()
    assert not ui.exception
    assert "saved" not in ui.session_state


def test_unreadable_policy_is_reported_without_overwriting_settings():
    ui = AppTest.from_string(SCRIPT)
    ui.session_state["callback"] = "limit"
    ui.session_state["fixture:limit"] = 7
    ui.session_state["fail-read"] = True
    ui.run()
    assert not ui.exception
    assert ui.session_state["fixture:error"] is True
    assert "saved" not in ui.session_state


def test_switching_nodes_preserves_current_shared_settings():
    ui = AppTest.from_string(SCRIPT).run()
    ui.number_input(key="trim:scheduling:local:model:limit").set_value(9).run()
    ui.selectbox(key="node").set_value("sft").run()
    ui.number_input(key="sft:scheduling:local:model:wait").set_value(159).run()
    ui.selectbox(key="node").set_value("trim").run()
    assert not ui.exception
    assert ui.number_input(key="trim:scheduling:local:model:limit").value == 9
    assert ui.number_input(key="trim:scheduling:local:model:wait").value == 159
