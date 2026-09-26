from streamlit.testing.v1 import AppTest


def screen():
    import streamlit as st
    from lib.presentation.streamlit.i18n import install_streamlit_localization
    from lib.presentation.streamlit.workflow_node_settings import render_agent_verification
    st.session_state["ui_language"] = "en"
    st.session_state.setdefault("workflow-agent-mode:test", "isolated")
    install_streamlit_localization()
    def check():
        st.session_state["checks"] = st.session_state.get("checks", 0) + 1
        return {"ready": False, "reason": "daemon_unavailable"}
    render_agent_verification("test", {"isolated_configured": True}, check)


def test_environment_check_is_explicit_and_failure_is_english():
    app = AppTest.from_function(screen).run()
    assert not app.exception and "checks" not in app.session_state
    app.button(key="agent-check:test").click().run()
    assert not app.exception and app.session_state["checks"] == 1
    assert any(item.value == "The container service is unavailable. Start Docker first."
               for item in app.warning)
