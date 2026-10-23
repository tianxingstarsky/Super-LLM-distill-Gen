"""Reading, review and delivery keep the exact task and verified version."""
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest


def result_actions_screen():
    import streamlit as st
    from lib.presentation.streamlit.i18n import install_streamlit_localization
    from lib.presentation.streamlit.workflow_report_panel import _render_report_actions
    install_streamlit_localization()
    st.session_state.setdefault("ws", "fixture")
    st.session_state.setdefault("workflow-result-view:run-a", "样本报告")
    st.session_state.setdefault("calls", [])

    class Workflow:
        def state(self, run_id):
            assert run_id == "run-a"
            return {"status": st.session_state.get("status", "completed"),
                    "targets": ["sft", "cot", "multiturn", "agent"]}

        def is_active(self, run_id):
            return st.session_state.get("active", False)

        def package_inventory(self, run_id):
            st.session_state["calls"].append(("verify", run_id))
            if st.session_state.get("tampered"):
                raise ValueError("artifact_integrity_error")
            return {"manifest": {"counts": {"sft": 1, "cot": 1, "multiturn": 1, "agent": 1},
                                 "negative_counts": {"agent": 1}},
                    "files": [{"name": name + ".jsonl"} for name in
                              ("sft", "cot", "multiturn", "agent", "agent.negative")]}

    class Human:
        def review_session(self, run_id):
            st.session_state["calls"].append(("review_session", run_id))
            return "session-a"

        def session(self, session_id):
            return {"rounds": [{"id": "round-a", "run_id": "run-a"}]}

    _render_report_actions(Workflow(), "run-a", st.session_state.get("target", "sft"),
                           {"finalized": st.session_state.get("finalized", True)},
                           human_application=Human())


def screen(**state):
    app = AppTest.from_function(result_actions_screen, default_timeout=15)
    for key, value in state.items():
        app.session_state[key] = value
    app.run()
    assert not app.exception
    return app


@pytest.mark.parametrize("target,selector,mode", [
    ("sft", "sft-review-run", "SFT 数据调整"),
    ("agent", None, None),
    ("agent_negative", None, None),
])
def test_report_review_opens_exact_existing_queue_after_verification(target, selector, mode):
    app = screen(target=target)
    app.button(key=f"workflow-report-review:run-a:{target}").click().run()
    assert not app.exception
    assert app.session_state["calls"] == [("verify", "run-a")]
    if selector:
        assert app.session_state[selector] == "run-a"
        assert app.session_state["review-mode:fixture"] == mode
    else:
        assert app.session_state["workflow-open-run"] == {"workspace": "fixture", "run_id": "run-a"}
        assert app.session_state["agent-review:fixture:run-a:kind"] == (
            "失败轨迹" if target == "agent_negative" else "训练候选")


@pytest.mark.parametrize("target", ["cot", "multiturn"])
def test_report_correction_uses_target_without_redirecting_to_sft(target):
    app = screen(target=target)
    assert not any("workflow-report-review:" in (button.key or "") for button in app.button)
    app.button(key=f"workflow-report-correct:run-a:{target}").click().run()
    assert not app.exception
    assert app.session_state["calls"] == [("verify", "run-a"), ("review_session", "run-a")]
    assert app.session_state["human-target:human:fixture:session-a:round-a"] == target
    assert app.session_state["task-return:fixture:human:session-a"] == {
        "run_id": "run-a", "view": "样本报告", "target": target, "limit": 6, "page": 1}


@pytest.mark.parametrize("state", [{"finalized": False}, {"status": "running"}, {"active": True}])
def test_unsealed_or_active_report_has_no_review_or_export_actions(state):
    app = screen(**state)
    assert not app.button
    assert app.session_state["calls"] == []


@pytest.mark.parametrize("action", ["review", "correct"])
def test_report_rechecks_changed_artifacts_at_click_and_never_opens_correction(action):
    app = screen()
    app.session_state["tampered"] = True
    app.button(key=f"workflow-report-{action}:run-a:sft").click().run()
    assert not app.exception and app.error
    assert app.session_state["calls"] == [("verify", "run-a")]
    assert "human-open-session" not in app.session_state
    assert "sft-review-run" not in app.session_state


def test_export_handoff_remembers_report_type_without_starting_packaging():
    app = screen(target="cot")
    app.button(key="workflow-report-package:run-a:cot").click().run()
    assert not app.exception
    assert app.session_state["nav"] == "输出打包"
    assert app.session_state["package-run:fixture"] == "run-a"
    assert app.session_state["package-target:fixture:run-a"] == "cot"
    assert app.session_state["task-return:fixture:package:run-a"]["view"] == "样本报告"
    assert app.session_state["calls"] == []


def test_report_actions_are_translated():
    app = screen(ui_language="en")
    assert [button.label for button in app.button] == [
        "Export training data", "Review this dataset type", "Scoring and correction"]


@pytest.mark.parametrize("view", ["运行过程", "样本报告"])
def test_return_restores_reading_context_and_removes_human_route(monkeypatch, view):
    from lib.presentation.streamlit import review_navigation as navigation
    state = {"ws": "fixture", "nav": "任务管理", "workflow-result-view:run-a": view,
             "workflow-report-target:run-a": "cot", "workflow-report-page:run-a": 420,
             "workflow-report-limit:run-a": 24}
    query = {"page": "人工问答增强", "human": "session-a", "lang": "en"}
    widgets = SimpleNamespace(session_state=state, query_params=query)
    monkeypatch.setattr(navigation, "st", widgets)
    navigation.remember_task_return("human:session-a", "run-a", widgets=widgets)
    state.pop("workflow-result-view:run-a")  # Streamlit unmounts the report widget.
    state.pop("workflow-report-target:run-a")
    state.pop("workflow-report-limit:run-a")
    # Opening the collection from a separate review page keeps the reading origin.
    navigation.remember_task_return("human:session-a", "run-a", widgets=widgets)
    navigation.return_to_task("run-a", destination="human:session-a")
    assert state["workflow-open-run"] == {"workspace": "fixture", "run_id": "run-a"}
    assert state["workflow-result-view:run-a"] == view
    assert state["workflow-report-target:run-a"] == "cot"
    assert state["workflow-report-page:run-a"] == 420
    assert state["workflow-report-limit:run-a"] == 24
    assert query == {"page": "任务管理", "lang": "en"}


def test_review_studio_return_uses_its_imported_task_not_a_different_history_selection(monkeypatch):
    from lib.presentation.streamlit import human_workspace_page as page
    session = {"id": "session-a", "rounds": [
        {"kind": "imported", "run_id": "run-a"}, {"kind": "revision", "run_id": "child-a"}]}
    widgets = SimpleNamespace(session_state={"task-return:fixture:human:session-a": {"run_id": "other"}})
    monkeypatch.setattr(page, "st", widgets)
    assert page._review_return_run(session, "fixture") == "run-a"
    assert page._review_return_run({"id": "empty", "rounds": []}, "fixture") is None
