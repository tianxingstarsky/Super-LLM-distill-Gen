from streamlit.testing.v1 import AppTest


def screen():
    from pathlib import Path
    from types import SimpleNamespace
    import streamlit as st
    from lib.presentation.streamlit.dataset_browser_page import render_dataset_preview
    from lib.presentation.streamlit.i18n import install_streamlit_localization
    st.session_state["ui_language"] = "en"
    install_streamlit_localization()
    def selected_samples(key):
        file = st.selectbox("Test file", ["工作流.jsonl", "Other.jsonl"])
        count = 3 if file == "工作流.jsonl" else 1
        return Path(file), [{"id": "工作流", "messages": [
            {"role": "user", "content": f"Question {index}"},
            {"role": "assistant", "content": f"Answer {index}"}]} for index in range(count)]
    render_dataset_preview(SimpleNamespace(task_runs=lambda: []), "test", selected_samples, show_title=False)


def test_file_navigation_keeps_boundaries_and_file_specific_position():
    app = AppTest.from_function(screen).run()
    assert not app.exception and app.button(key="file-preview-prev:test").disabled
    assert app.button(key="file-preview-next:test").label == "Next"
    app.button(key="file-preview-next:test").click().run()
    assert app.number_input[0].value == 2
    app.number_input[0].set_value(3).run()
    assert app.button(key="file-preview-next:test").disabled
    app.selectbox[0].select("Other.jsonl").run()
    assert not app.exception and app.number_input[0].value == 1
    app.selectbox[0].select("工作流.jsonl").run()
    assert not app.exception and app.number_input[0].value == 3
    app.button(key="file-preview-prev:test").click().run()
    assert app.number_input[0].value == 2
    markup = "".join(item.value for item in app.get("html"))
    assert '<b data-user-content>工作流</b>' in markup
    assert '<strong data-user-content>工作流.jsonl</strong>' in markup
