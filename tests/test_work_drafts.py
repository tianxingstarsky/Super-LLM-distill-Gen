"""The compact draft menu exposes actions only for work that exists."""
from streamlit.testing.v1 import AppTest

from lib.bootstrap.creation_drafts import creation_draft_application


def draft_menu(output):
    from pathlib import Path
    import streamlit as st
    from lib.bootstrap.creation_drafts import creation_draft_application
    from lib.presentation.streamlit.work_drafts import render_work_drafts

    with st.popover("工作草稿"):
        render_work_drafts(
            creation_draft_application(Path(output)), "default",
            lambda page: st.session_state.__setitem__("nav", page),
        )


def test_only_automatic_draft_has_no_empty_selector_and_can_be_continued(tmp_path):
    drafts = creation_draft_application(tmp_path)
    current = {"workflow-name:default": "客户任务 Keep 原文", "workflow-count:default": 50000}
    drafts.replace(current)

    ui = AppTest.from_function(draft_menu, args=(str(tmp_path),)).run()
    assert not ui.exception
    assert not list(ui.selectbox)
    assert not [button for button in ui.button if button.key == "work-draft-open:default"]
    assert not ui.button(key="work-draft-current:default").disabled
    ui.button(key="work-draft-current:default").click().run()
    assert not ui.exception
    assert ui.session_state["nav"] == "自动工作流"
    assert drafts.load() == current


def test_named_draft_appears_when_saved_and_restore_still_preserves_current_work(tmp_path):
    drafts = creation_draft_application(tmp_path)
    saved = {"workflow-name:default": "Saved work", "workflow-count:default": 1200}
    drafts.replace(saved)
    ui = AppTest.from_function(draft_menu, args=(str(tmp_path),)).run()
    assert not ui.exception
    assert not list(ui.selectbox)

    saved_id = drafts.save_snapshot("Saved work", values=saved)
    edited = {"workflow-name:default": "Current work", "workflow-count:default": 50000}
    drafts.replace(edited)
    ui.run()
    assert not ui.exception
    ui.selectbox(key="work-draft-selected:default:0").set_value(saved_id).run()
    ui.button(key="work-draft-open:default").click().run()
    assert not ui.exception
    assert drafts.load() == saved
    assert ui.session_state["workflow-form-draft:default"] == saved
    backup = ui.session_state["work-draft-switch-backup:default"]
    assert backup != saved_id
    ui.button(key="work-draft-return:default").click().run()
    assert not ui.exception
    assert drafts.load() == edited
