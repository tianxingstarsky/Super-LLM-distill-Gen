"""Concurrent workflow tasks remain easy to find in the current workspace."""
from __future__ import annotations

import pytest
from streamlit.testing.v1 import AppTest

from lib.presentation.streamlit.task_management_page import _quick_switch_runs


def test_small_task_list_keeps_full_selection_without_duplicate_quick_switch():
    script = '''
import streamlit as st
from unittest.mock import patch
from lib.presentation.streamlit import task_management_page as page
class Application:
    def task_runs(self):
        return [{"id": str(number), "name": "Task " + str(number), "status": "running",
                 "targets": ["sft"]} for number in range(3)]
st.session_state["ws"] = "fixture"
st.session_state.setdefault("task-center-focus:fixture", False)
with patch.object(page, "render_run", lambda *args, **kwargs: None):
    page.render_task_management(Application(), "fixture", lambda _: None, lambda: None)
'''
    ui = AppTest.from_string(script).run()
    assert not ui.exception
    assert len([button for button in ui.button if button.key.startswith("task-card:fixture:")]) == 3
    assert not any(widget.key == "task-quick-select:fixture" for widget in ui.selectbox)
    ui.toggle(key="task-center-focus:fixture").set_value(True).run()
    assert not ui.exception
    assert not any(widget.key == "task-quick-select:fixture" for widget in ui.selectbox)
    assert len([button for button in ui.button if button.key.startswith("task-card:fixture:")]) == 3
    ui.button(key="task-card:fixture:2").click().run()
    assert not ui.exception
    assert ui.session_state["task-center-run:fixture"] == "2"


def test_quick_switch_shows_recent_distinct_actionable_runs_only():
    runs = [
        {"id": f"running-{number}", "status": "running",
         "updated_at": f"2026-10-03T{number:02d}:00:00"} for number in range(8)
    ] + [
        {"id": f"attention-{number}", "status": "needs_attention",
         "updated_at": f"2026-10-03T{number:02d}:30:00"} for number in range(5)
    ] + [
        {"id": "running-7", "status": "running", "updated_at": "2026-10-03T07:00:00"},
        {"id": "done", "status": "completed", "updated_at": "2026-10-03T23:00:00"},
    ]
    attention, active = _quick_switch_runs(runs)
    assert attention[1] == 5 and active[1] == 8
    assert [row["id"] for row in attention[0]] == ["attention-4", "attention-3", "attention-2"]
    assert [row["id"] for row in active[0]] == ["running-7", "running-6", "running-5"]


def test_quick_group_reveals_every_unfinished_task_without_leaving_page():
    script = """
import streamlit as st
from unittest.mock import patch
from lib.presentation.streamlit import task_management_page as page

class Application:
    def task_runs(self):
        return [{"id": f"active-{number}", "name": f"Task {number}",
                 "status": "running", "created_at": f"2026-10-03T{number:02d}:00:00",
                 "updated_at": f"2026-10-03T{number:02d}:00:00", "targets": ["sft"]}
                for number in range(8)]

st.session_state["ws"] = "fixture"
st.session_state["ui_language"] = "en"
with patch.object(page, "render_run", lambda application, run_id, begin, embedded=False: st.caption(run_id)):
    page.render_task_management(Application(), "fixture", lambda _: None, lambda: None)
"""
    ui = AppTest.from_string(script, default_timeout=30).run()
    assert not ui.exception
    switcher = ui.selectbox(key="task-quick-select:fixture")
    assert "View all unfinished tasks" in switcher.options
    assert len([option for option in switcher.options if option.startswith("Task ")]) == 3
    ui.toggle(key="task-center-focus:fixture").set_value(True).run()
    ui.text_input(key="task-center-search:fixture").set_value("nothing matches").run()
    ui.selectbox(key="task-quick-select:fixture").set_value("group:active").run()
    assert not ui.exception
    assert ui.session_state["task-center-filter:fixture"] == "未结束"
    assert ui.session_state["task-center-search:fixture"] == ""
    assert ui.session_state["task-center-focus:fixture"] is False
    assert len([button for button in ui.button if button.key.startswith("task-card:fixture:")]) == 8


def test_shortcut_clears_filters_and_locates_older_active_run_on_later_page():
    script = """
import streamlit as st
from unittest.mock import patch
from lib.presentation.streamlit import task_management_page as page

class Application:
    def task_runs(self):
        completed = [{"id": f"done-{number:03d}", "name": f"Finished {number}",
                      "status": "completed", "created_at": "2026-10-03T12:00:00",
                      "updated_at": "2026-10-03T12:00:00", "targets": ["sft"]}
                     for number in range(120)]
        return completed + [
            {"id": "old-running", "name": "Older active request", "status": "running",
             "created_at": "2026-09-30T12:00:00", "updated_at": "2026-10-03T13:00:00",
             "targets": ["sft"]},
            {"id": "attention", "name": "Review this request", "status": "needs_attention",
             "created_at": "2026-09-29T12:00:00", "updated_at": "2026-10-03T14:00:00",
             "targets": ["cpt"]},
        ]

st.session_state["ws"] = "fixture"
with patch.object(page, "render_run", lambda application, run_id, begin, embedded=False: st.caption(f"DETAIL {run_id}")):
    page.render_task_management(Application(), "fixture", lambda _: None, lambda: None)
"""
    ui = AppTest.from_string(script, default_timeout=30).run()
    assert not ui.exception
    assert any("Older active request" in option for option in ui.selectbox(key="task-quick-select:fixture").options)
    ui.segmented_control(key="task-center-filter:fixture").set_value("已完成").run()
    ui.text_input(key="task-center-search:fixture").set_value("Finished").run()
    assert ui.session_state["task-center-run:fixture"] != "old-running"

    ui.selectbox(key="task-quick-select:fixture").set_value("old-running").run()
    assert not ui.exception
    assert ui.session_state["task-center-filter:fixture"] == "全部"
    assert ui.session_state["task-center-search:fixture"] == ""
    assert ui.session_state["task-center-page:fixture"] == 2
    assert ui.session_state["task-center-run:fixture"] == "old-running"
    assert ui.number_input(key="task-center-page:fixture:jump").value == 3


def test_page_number_jump_opens_distant_tasks_and_tracks_navigation():
    script = """
import streamlit as st
from unittest.mock import patch
from lib.presentation.streamlit import task_management_page as page

class Application:
    def task_runs(self):
        return [{"id": f"run-{number:05d}", "name": f"Task {number:05d}",
                 "status": "completed", "created_at": "2026-10-03T12:00:00",
                 "updated_at": "2026-10-03T12:00:00", "targets": ["sft"]}
                for number in range(10_051)]

st.session_state["ws"] = "fixture"
with patch.object(page, "render_run", lambda application, run_id, begin, embedded=False: st.caption(f"DETAIL {run_id}")):
    page.render_task_management(Application(), "fixture", lambda _: None, lambda: None)
"""
    ui = AppTest.from_string(script, default_timeout=30).run()
    assert not ui.exception

    jump = ui.number_input(key="task-center-page:fixture:jump")
    assert jump.value == 1
    jump.set_value(202).run()
    assert not ui.exception
    assert ui.session_state["task-center-page:fixture"] == 201
    assert ui.session_state["task-center-run:fixture"] == "run-10050"
    assert ui.button(key="task-card:fixture:run-10050")

    ui.button(key="task-center-page:fixture:previous").click().run()
    assert not ui.exception
    assert ui.number_input(key="task-center-page:fixture:jump").value == 201
    assert ui.session_state["task-center-run:fixture"] == "run-10000"

    ui.text_input(key="task-center-search:fixture").set_value("Task 10050").run()
    assert not ui.exception
    assert ui.session_state["task-center-page:fixture"] == 0
    assert ui.session_state["task-center-run:fixture"] == "run-10050"
    assert not ui.get("number_input")


def test_workspace_change_never_shows_other_workspaces_shortcuts():
    script = """
import streamlit as st
from unittest.mock import patch
from lib.presentation.streamlit import task_management_page as page

class Application:
    def __init__(self, workspace): self.workspace = workspace
    def task_runs(self):
        return [{"id": self.workspace + "-run", "name": self.workspace + " private task",
                 "status": "running", "created_at": "2026-10-03T12:00:00",
                 "updated_at": "2026-10-03T12:00:00", "targets": ["sft"]}] + [
                    {"id": self.workspace + "-done-" + str(number), "name": "Finished " + str(number),
                     "status": "completed", "targets": ["sft"]} for number in range(3)]

workspace = st.selectbox("Workspace", ["alpha", "beta"], key="ws")
st.session_state.setdefault("task-center-focus:" + workspace, True)
st.button("Stale shortcut", key="stale-shortcut",
          on_click=page._switch_run, args=("alpha", "alpha-other"))
st.button("Stale group", key="stale-group",
          on_click=page._show_quick_group, args=("alpha", "未结束"))
with patch.object(page, "render_run", lambda application, run_id, begin, embedded=False: st.caption(run_id)):
    page.render_task_management(Application(workspace), workspace, lambda _: None, lambda: None)
"""
    ui = AppTest.from_string(script, default_timeout=30).run()
    assert not ui.exception
    assert ui.selectbox(key="task-quick-select:alpha")
    ui.selectbox(key="ws").select("beta").run()
    assert not ui.exception
    assert ui.selectbox(key="task-quick-select:beta")
    assert not [widget for widget in ui.selectbox if widget.key.startswith("task-quick-select:alpha")]
    assert ui.session_state["task-center-run:beta"] == "beta-run"
    ui.button(key="stale-shortcut").click().run()
    assert not ui.exception
    assert ui.session_state["task-center-run:alpha"] == "alpha-run"
    assert ui.session_state["task-center-run:beta"] == "beta-run"
    ui.button(key="stale-group").click().run()
    assert not ui.exception
    with pytest.raises(KeyError):
        ui.session_state["task-center-filter:alpha"]
    assert ui.session_state["task-center-filter:beta"] == "全部"


def test_shortcut_translates_interface_without_rewriting_task_name():
    script = """
import streamlit as st
from unittest.mock import patch
from lib.presentation.streamlit import task_management_page as page

class Application:
    def task_runs(self):
        return [{"id": "one", "name": "客户任务 Keep 原文", "status": "running",
                 "created_at": "2026-10-03T12:00:00", "updated_at": "2026-10-03T12:00:00",
                 "targets": ["sft"]}] + [
                    {"id": "done-" + str(number), "name": "Finished " + str(number),
                     "status": "completed", "targets": ["sft"]} for number in range(3)]

st.session_state["ws"] = "fixture"
st.session_state["ui_language"] = "en"
st.session_state.setdefault("task-center-focus:fixture", True)
with patch.object(page, "render_run", lambda application, run_id, begin, embedded=False: st.caption(run_id)):
    page.render_task_management(Application(), "fixture", lambda _: None, lambda: None)
"""
    ui = AppTest.from_string(script, default_timeout=30).run()
    assert not ui.exception
    shortcut = ui.selectbox(key="task-quick-select:fixture")
    assert shortcut.label == "Switch run"
    assert any("客户任务 Keep 原文" in option and "Running" in option for option in shortcut.options)
    assert not [button for button in ui.button if button.key.startswith("task-quick:")]
def test_soft_count_does_not_make_completed_task_look_incomplete():
    from lib.domain.workflow_graph import execution_graph
    from lib.presentation.streamlit.task_management_page import _run_card_html
    stages, _ = execution_graph(["sft"])
    run = {"id": "soft-task", "status": "completed", "targets": ["sft"],
           "stages": {key: {"status": "completed"} for key in stages},
           "production": {"version": 2, "goals": {"sft": {"goal": 1000, "eligible": 20}}}}
    heading, detail = _run_card_html(run)
    assert 'data-status="completed"' in heading and "完成" in heading
    assert "产出 / 期望" in detail and "20/1,000" in detail
    assert f'已完成节点 <b>{len(stages)}/{len(stages)}</b>' in detail
    assert detail.index('class="df-task-card-output"') < detail.index('class="df-task-card-progress"')
    assert "width:100%" in detail and 'aria-label="已完成节点"' in detail


def test_compact_target_types_keep_full_accessible_names_and_user_source_text():
    from lib.presentation.streamlit.i18n import translate_markup
    from lib.presentation.streamlit.task_management_page import _run_card_html

    _, detail = _run_card_html({
        "id": "compact", "status": "queued", "targets": ["cpt", "sft", "dpo", "cot", "agent"],
        "source_mode": "document", "source_names": ['客户_[SFT] & "来源".md'],
    }, language="en")
    for short, full in (("CPT", "CPT pretraining text"),
                        ("SFT", "SFT instructions and conversations"),
                        ("DPO", "DPO preference pairs")):
        assert f'title="{full}"' in detail
        assert f'<span aria-hidden="true">{short}</span>' in detail
        assert f'<span class="df-task-sr-only">{full}</span>' in detail
    assert '<span aria-hidden="true">+2</span>' in detail
    assert 'title="CoT reasoning text · Agent verified traces"' in detail
    localized = translate_markup(detail, "en")
    assert '客户_[SFT] &amp; &quot;来源&quot;.md' in localized
    assert 'data-user-content' in localized


def test_task_filter_has_one_empty_state_and_native_titles_select_exact_run():
    script = """
import streamlit as st
from unittest.mock import patch
from lib.presentation.streamlit import task_management_page as page
class Application:
    def task_runs(self):
        return [{"id": identifier, "name": "客户任务 Keep 原文 " + identifier,
                 "status": "failed", "targets": ["cpt", "sft", "dpo"]}
                for identifier in ("one", "two")]
st.session_state["ws"] = "fixture"
st.session_state.setdefault("task-center-focus:fixture", False)
with patch.object(page, "render_run", lambda application, run_id, begin, embedded=False: st.caption("DETAIL " + run_id)):
    page.render_task_management(Application(), "fixture", lambda _: None, lambda: None)
"""
    ui = AppTest.from_string(script).run()
    assert not ui.exception
    title = ui.button(key="task-card:fixture:two")
    assert "客户任务 Keep 原文 two" in title.label
    assert title.label.endswith("→")
    title.click().run()
    assert not ui.exception
    assert ui.session_state["task-center-run:fixture"] == "two"
    assert any(item.value == "DETAIL two" for item in ui.caption)
    ui.text_input(key="task-center-search:fixture").set_value("nothing matches").run()
    assert not ui.exception
    notes = [item.proto.body for item in ui.get("html") if 'class="df-task-no-match"' in item.proto.body]
    assert len(notes) == 1 and "没有符合当前筛选条件" in notes[0]
    assert "选择左侧任务" not in notes[0]
    assert ui.session_state["task-center-run:fixture"] is None
    assert not [button for button in ui.button if button.key.startswith("task-card:")]
    assert not [item for item in ui.caption if item.value.startswith("DETAIL ")]


def test_single_task_has_no_duplicate_switcher_across_expanded_view_changes():
    script = """
import streamlit as st
from unittest.mock import patch
from lib.presentation.streamlit import task_management_page as page
class Application:
    def task_runs(self):
        return [{"id": "one", "name": "Saved run", "status": "failed", "targets": ["sft"]}]
st.session_state["ws"] = "fixture"
with patch.object(page, "render_run", lambda application, run_id, begin, embedded=False: st.caption(run_id)):
    page.render_task_management(Application(), "fixture", lambda _: None, lambda: None)
"""
    ui = AppTest.from_string(script).run()
    assert not ui.exception
    assert not [widget for widget in ui.selectbox if widget.key == "task-quick-select:fixture"]
    ui.run()
    assert not ui.exception
    assert not [widget for widget in ui.selectbox if widget.key == "task-quick-select:fixture"]
    ui.toggle(key="task-center-focus:fixture").set_value(True).run()
    assert not ui.exception
    assert not [widget for widget in ui.selectbox if widget.key == "task-quick-select:fixture"]
    assert "Saved run" in ui.button(key="task-card:fixture:one").label
    ui.toggle(key="task-center-focus:fixture").set_value(False).run()
    assert not ui.exception
    assert not [widget for widget in ui.selectbox if widget.key == "task-quick-select:fixture"]
