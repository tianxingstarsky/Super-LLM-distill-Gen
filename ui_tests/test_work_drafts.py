"""Reopen separate unfinished work after the UI session has ended."""
from pathlib import Path

from streamlit.testing.v1 import AppTest

from lib.bootstrap.creation_drafts import creation_draft_application


def screen(output):
    import streamlit as st
    from pathlib import Path
    from lib.bootstrap.creation_drafts import creation_draft_application
    from lib.presentation.streamlit.i18n import install_streamlit_localization
    from lib.presentation.streamlit.work_drafts import render_work_drafts

    install_streamlit_localization()

    def navigate(page):
        st.session_state["nav"] = page

    render_work_drafts(creation_draft_application(Path(output)), "default", navigate)


def test_new_session_opens_exact_named_draft_without_mixing_current_work(tmp_path):
    application = creation_draft_application(tmp_path)
    first = {"workflow-name:default": "Manual dataset", "workflow-count:default": 50000,
             "workflow-source-mode:default": "文档资料", "workflow-sources:default:文档资料": ["manual.txt"]}
    application.update(first)
    first_id = application.save_snapshot()
    application.update({"workflow-name:default": "Open brief", "workflow-open-brief:default": "Other task"})
    second_id = application.save_snapshot()
    ui = AppTest.from_function(screen, args=(str(tmp_path),)).run()
    assert not ui.exception
    assert set(ui.selectbox[0].options)
    ui.selectbox[0].set_value(first_id).run()
    ui.session_state["workflow-open-brief:default"] = "Stale other task"
    ui.session_state["workflow-node-bindings:default"] = {"sft": {"model": "old"}}
    ui.session_state["job:default:running-id"] = "Running task is independent"
    ui.button(key="work-draft-open:default").click().run()
    assert not ui.exception
    assert ui.session_state["nav"] == "自动工作流"
    assert ui.session_state["workflow-form-draft:default"] == first
    assert "workflow-open-brief:default" not in ui.session_state
    assert "workflow-node-bindings:default" not in ui.session_state
    assert ui.session_state["job:default:running-id"] == "Running task is independent"
    assert creation_draft_application(tmp_path).load() == first
    assert {row["id"] for row in application.snapshots()} == {first_id, second_id}


def test_work_manager_paginates_all_saved_drafts_without_removing_history(tmp_path):
    application = creation_draft_application(tmp_path)
    application.update({"workflow-name:default": "Draft"})
    identifiers = {application.save_snapshot(f"Draft {number}") for number in range(22)}
    ui = AppTest.from_function(screen, args=(str(tmp_path),)).run()
    assert not ui.exception
    assert len(ui.selectbox[0].options) == 20
    ui.button(key="work-drafts-page:default:next").click().run()
    assert not ui.exception and len(ui.selectbox[0].options) == 2
    assert {row["id"] for row in application.snapshots()} == identifiers


def test_english_work_manager_translates_empty_draft_controls(tmp_path):
    ui = AppTest.from_function(screen, args=(str(tmp_path),))
    ui.session_state["ui_language"] = "en"
    ui.run()
    assert not ui.exception
    assert ui.button(key="work-draft-current:default").label == "Continue current draft"
    assert "Untitled work" in "".join(str(item.value) for item in ui.get("html"))
