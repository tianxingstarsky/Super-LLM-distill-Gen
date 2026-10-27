"""Report paging and view changes must not generate or lose the selected run."""
from streamlit.testing.v1 import AppTest


SCRIPT = '''
import streamlit as st
from unittest.mock import patch
from lib.presentation.streamlit import workflow_report_panel as page
from lib.presentation.streamlit.i18n import install_streamlit_localization
install_streamlit_localization()
st.session_state.setdefault("ws", "fixture")
st.session_state.setdefault("task-center-focus:fixture", False)
st.session_state.setdefault("ui_language", "zh")
st.session_state.setdefault("reads", [])
class Reports:
    def overview(self, run_id):
        if st.session_state.get("broken"):
            raise ValueError("manifest_hash_mismatch")
        return {"targets": [{"target": "cot"}, {"target": "multiturn"}]}
    def samples(self, run_id, target, offset=0, limit=12):
        st.session_state["reads"].append((target, offset, limit))
        total = 1000000 if target == "cot" else 2
        if st.session_state.get("preparing"):
            return {"indexing": False, "preparation": {"status": "failed", "retryable": True},
                    "total": None, "rows": [], "finalized": False}
        if st.session_state.get("live"):
            return {"total": None, "rows": [{"id": "recent"}], "finalized": False}
        return {"total": total, "rows": [], "finalized": True}
    def html(self, run_id, target, **kwargs):
        return "<!doctype html><p>Complete real sample</p>"
    def html_snapshot(self, overview, page, language="zh"):
        assert not page.get("indexing")
        return "<!doctype html><p>Complete real sample</p>"
    def html_embedded_snapshot(self, overview, page, language="zh"):
        assert not page.get("indexing")
        return '<section data-user-content>Complete real sample</section>'
    def retry(self, run_id, target):
        st.session_state["preparing"] = False
class Application:
    reports = Reports()
st.toggle("Expand", key="task-center-focus:fixture")
with patch.object(page.st, "html", lambda *args, **kwargs: None):
    if page.render_run_report_choice(Application(), "real-run", embedded=True):
        page.render_workflow_report(Application(), "real-run", embedded=True)
    else:
        st.caption("RUN_PROGRESS")
'''


def opened(language="zh"):
    app = AppTest.from_string(SCRIPT, default_timeout=20)
    app.session_state["ui_language"] = language
    app.run()
    assert not app.exception
    app.segmented_control(key="workflow-result-view:real-run").set_value("样本报告").run()
    assert not app.exception
    return app


def initial_choice_screen():
    import streamlit as st
    from lib.presentation.streamlit.workflow_report_panel import render_run_report_choice
    class Application:
        reports = object()
        def state(self, run_id):
            return {"status": st.session_state.get("status", "completed")}
        def is_active(self, run_id):
            return st.session_state.get("active", False)
    if st.toggle("Away", key="away"):
        st.caption("Another page")
    else:
        render_run_report_choice(Application(), "run-a")


def test_completed_run_opens_report_but_explicit_process_choice_survives_navigation():
    app = AppTest.from_function(initial_choice_screen).run()
    assert not app.exception
    assert app.segmented_control(key="workflow-result-view:run-a").value == "样本报告"
    app.segmented_control(key="workflow-result-view:run-a").set_value("运行过程").run()
    app.toggle(key="away").set_value(True).run()
    app.run()  # Simulate widget cleanup while the report is unmounted.
    app.toggle(key="away").set_value(False).run()
    assert not app.exception
    assert app.segmented_control(key="workflow-result-view:run-a").value == "运行过程"


def test_active_or_failed_tasks_keep_process_as_initial_view():
    for state in ({"status": "running"}, {"status": "needs_attention"}, {"status": "failed"}, {"active": True}):
        app = AppTest.from_function(initial_choice_screen)
        for key, value in state.items():
            app.session_state[key] = value
        app.run()
        assert not app.exception
        assert app.segmented_control(key="workflow-result-view:run-a").value == "运行过程"


def test_report_expands_reading_and_restores_original_focus_on_return():
    app = opened()
    assert app.session_state["task-center-focus:fixture"] is True
    assert app.session_state["workflow-report-refresh:real-run"] is False
    assert app.session_state["ws"] == "fixture"
    app.segmented_control(key="workflow-result-view:real-run").set_value("运行过程").run()
    assert not app.exception
    assert app.session_state["task-center-focus:fixture"] is False
    assert any(item.value == "RUN_PROGRESS" for item in app.caption)


def test_million_sample_report_pages_a_bounded_slice_and_resets_on_target_change():
    app = opened()
    app.number_input(key="workflow-report-page:real-run:input").set_value(10000).run()
    assert not app.exception
    assert app.session_state["reads"][-1] == ("cot", 59994, 6)
    app.selectbox(key="workflow-report-target:real-run").set_value("multiturn").run()
    assert not app.exception
    assert app.session_state["workflow-report-page:real-run"] == 1
    assert app.session_state["reads"][-1] == ("multiturn", 0, 6)
    assert app.button(key="workflow-report-page:real-run:next").disabled


def test_report_english_labels_keep_canonical_options_and_paging():
    app = opened("en")
    assert "Sample report" in app.segmented_control(key="workflow-result-view:real-run").options
    assert app.selectbox(key="workflow-report-target:real-run").options == ["CoT", "Multi-turn conversation"]
    assert app.number_input(key="workflow-report-page:real-run:input").label == "Report page"
    assert any("1,000,000 records" in item.value for item in app.caption)
    app.button(key="workflow-report-page:real-run:next").click().run()
    assert not app.exception
    assert app.session_state["reads"][-1] == ("cot", 6, 6)


def test_integrity_error_has_return_action_and_does_not_fall_back_to_other_data():
    app = AppTest.from_string(SCRIPT, default_timeout=20)
    app.session_state["broken"] = True
    app.run()
    app.segmented_control(key="workflow-result-view:real-run").set_value("样本报告").run()
    assert not app.exception
    assert app.error and app.session_state["reads"] == []
    app.button(key="workflow-report-error-process:real-run").click().run()
    assert not app.exception
    assert app.session_state["workflow-result-view:real-run"] == "运行过程"
    assert app.session_state["task-center-focus:fixture"] is False


def test_live_report_has_no_fake_million_sample_pagination():
    app = AppTest.from_string(SCRIPT, default_timeout=20)
    app.session_state["live"] = True
    app.run()
    app.segmented_control(key="workflow-result-view:real-run").set_value("样本报告").run()
    assert not app.exception and not app.error
    assert app.number_input(key="workflow-report-page:real-run:input").disabled
    assert any("最近已保存输出" in item.value for item in app.caption)


def test_preparing_report_never_shows_zero_sample_export_and_retry_is_read_only():
    app = AppTest.from_string(SCRIPT, default_timeout=20)
    app.session_state["preparing"] = True
    app.run()
    app.segmented_control(key="workflow-result-view:real-run").set_value("样本报告").run()
    assert not app.exception
    assert app.error and not app.number_input
    app.button(key="workflow-report-retry:real-run:cot").click().run()
    assert not app.exception and not app.error
    assert app.number_input(key="workflow-report-page:real-run:input")


def test_background_read_does_not_lose_a_high_page_cursor_when_editor_is_absent():
    app = opened()
    app.session_state["preparing"] = True
    app.number_input(key="workflow-report-page:real-run:input").set_value(10000).run()
    assert not app.exception and not app.number_input
    app.run()
    assert app.session_state["workflow-report-page:real-run"] == 10000
    app.button(key="workflow-report-retry:real-run:cot").click().run()
    assert not app.exception
    assert app.number_input(key="workflow-report-page:real-run:input").value == 10000
    assert app.session_state["reads"][-1] == ("cot", 59994, 6)
