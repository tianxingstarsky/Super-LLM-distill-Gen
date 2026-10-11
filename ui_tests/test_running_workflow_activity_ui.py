"""Visible, persisted activity during long parsing, without API or workers."""
from copy import deepcopy
from pathlib import Path
import re

import pytest
from streamlit.testing.v1 import AppTest


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


RUN_ID = "a" * 32


def activity_application():
    import streamlit as st
    from lib.domain.workflow_graph import execution_graph

    recipe = {"version": 18, "targets": ["sft", "dpo", "cpt"], "sources": [{"name": "用户资料.docx"}],
              "qa_director": {"enabled": True}, "reasoning_trim": {"enabled": True},
              "review_repair": {"mode": "auto", "max_rounds": 2, "score_threshold": .8}}
    nodes, _ = execution_graph(recipe["targets"], qa_director=recipe["qa_director"], reasoning_trim=True,
                              recipe_version=18)
    st.session_state.setdefault("fixture-state", {
        "id": "a" * 32, "name": "正在处理真实资料", "status": "running", "targets": recipe["targets"],
        "created_at": "2026-10-11T03:30:00+00:00", "updated_at": "2026-10-11T03:36:00+00:00",
        "stages": {node: {"label": node, "status": "running" if node == "ingest" else "pending",
                          "done": 0, "total": 11 if node == "ingest" else 0} for node in nodes},
        "usage": {"generation": {"calls": 13}},
        "models": {"ingest.generation": {"model": "configured-parser"}},
        "source_library": {"parts": 0, "text_chars": 0,
                           "sources": [{"name": "用户资料.docx", "status": "parsing"}]},
        "events": [{"stage": "ingest", "kind": "model_started", "role": "generation"}],
    })
    st.session_state.setdefault("fixture-begin-calls", [])
    st.session_state.setdefault("fixture-stream-reads", [])

    class Application:
        def state(self, run_id):
            return deepcopy(st.session_state["fixture-state"])
        def recipe(self, run_id):
            return deepcopy(recipe)
        def is_active(self, run_id):
            return st.session_state["fixture-state"]["status"] == "running"
        def task_runs(self):
            row = self.state("a" * 32)
            return [{**row, "scheduled_stages": list(nodes), "source_mode": "document",
                     "source_names": ["用户资料.docx"], "source_brief": "", "recipe_readable": True}]
        def read_streams(self, run_id, *, stage):
            st.session_state["fixture-stream-reads"].append(stage)
            return []

    def begin(command):
        st.session_state["fixture-begin-calls"].append(command)

    return Application(), begin


def activity_screen(*, task_list=False):
    from unittest.mock import patch
    import streamlit as st
    from ui_tests.test_running_workflow_activity_ui import activity_application
    from lib.presentation.streamlit import workflow_canvas
    from lib.presentation.streamlit.i18n import install_streamlit_localization
    from lib.presentation.streamlit.task_management_page import render_task_management
    from lib.presentation.streamlit.workflow_page import render_run

    install_streamlit_localization()
    st.session_state["ws"] = "fixture"
    application, begin = activity_application()

    def canvas(**values):
        st.session_state["fixture-spec"] = values["spec"]
        return None

    with patch.object(workflow_canvas, "_canvas", canvas):
        if task_list:
            render_task_management(application, "fixture", begin, lambda: None)
        else:
            render_run(application, "a" * 32, begin)


def _activity_markup(ui):
    return next(item.proto.body for item in ui.get("html") if 'class="df-run-activity"' in item.proto.body)


def test_legacy_parsing_run_shows_model_work_and_opens_the_actual_stream_without_a_new_request():
    ui = AppTest.from_function(activity_screen, default_timeout=15).run()
    assert not ui.exception
    activity = _activity_markup(ui)
    assert "模型正在处理" in activity and "累计模型调用<b>13</b>" in activity
    assert "用户资料.docx" in activity and "0 / 11" in activity
    assert "%" not in activity
    ui.button(key=f"workflow-run-activity:{RUN_ID}").click().run()
    assert not ui.exception
    assert ui.session_state[f"workflow-stage:{RUN_ID}"] == "ingest"
    assert ui.session_state[f"canvas-open:live-canvas:{RUN_ID}"]
    assert ui.session_state["fixture-stream-reads"] and set(ui.session_state["fixture-stream-reads"]) == {"ingest"}
    assert ui.session_state["fixture-begin-calls"] == []


def test_saved_segments_update_while_the_file_count_stays_zero_and_ui_labels_translate():
    from ui_tests.test_human_workspace_ui import StaticMarkupText

    ui = AppTest.from_function(activity_screen, default_timeout=15)
    ui.session_state["ui_language"] = "en"
    ui.run()
    state = deepcopy(ui.session_state["fixture-state"])
    state["stages"]["ingest"].update(parsed_parts=3, input_chars=12_000, parsed_sources=0,
                                     current_source="用户资料.docx")
    state["usage"]["generation"]["calls"] = 14
    ui.session_state["fixture-state"] = state
    ui.run()
    assert not ui.exception
    activity = _activity_markup(ui)
    assert "Parsed segments<b>3</b>" in activity and "12,000" in activity and "0 / 11" in activity
    assert "Model calls so far<b>14</b>" in activity
    assert "用户资料.docx" in activity
    parser = StaticMarkupText()
    parser.feed(activity)
    assert not any(re.search(r"[\u4e00-\u9fff]", value) for value in parser.text), parser.text
    assert ui.session_state["fixture-begin-calls"] == []


def test_task_card_and_selected_detail_use_the_same_eight_node_graph_and_real_activity():
    ui = AppTest.from_function(activity_screen, kwargs={"task_list": True}, default_timeout=15).run()
    assert not ui.exception
    markup = "".join(item.proto.body for item in ui.get("html"))
    assert 'aria-valuemax="8" aria-valuenow="0"' in markup
    assert 'class="df-task-card-activity"' in markup
    assert "模型正在处理" in markup and "累计模型调用 <b>13</b>" in markup
    assert ui.session_state["task-center-run:fixture"] == RUN_ID
    assert len(ui.session_state["fixture-spec"]["nodes"]) == 8
    assert ui.session_state["fixture-begin-calls"] == []


@pytest.mark.parametrize("version,repair_only,expected", [(18, False, 8), (18, True, 3), (16, False, 7)])
def test_task_inventory_caches_small_activity_and_uses_the_frozen_recipe_graph(tmp_path, monkeypatch,
                                                                           version, repair_only, expected):
    from lib.infrastructure import workflow_task_inventory as inventory
    from lib.infrastructure.training_workflow import atomic_json, create_run, read_json, run_path

    source = tmp_path / "source.txt"
    source.write_text("A reusable document source.", encoding="utf-8")
    output = tmp_path / "out"
    run_id = create_run(output, sources=[source], targets=["sft", "dpo", "cpt"],
                        review_repair={"mode": "auto", "max_rounds": 2, "score_threshold": .8},
                        qa_director={"enabled": True}, reasoning_trim={"enabled": True})
    path = run_path(output, run_id)
    recipe = read_json(path / "recipe.json")
    recipe["version"] = version
    if repair_only:
        recipe["repair_inputs"] = [{"id": "fixture"}]
    atomic_json(path / "recipe.json", recipe)
    state = read_json(path / "state.json")
    state["status"] = "running"
    state["stages"]["ingest"].update(status="running", parsed_parts=3, input_chars=12_000,
                                    current_source="source.txt", parsed_sources=0)
    state["usage"] = {"generation": {"calls": 14, "completion_tokens": 100_000}}
    state["source_library"] = {"parts": 3, "text_chars": 12_000,
                               "sources": [{"name": "source.txt", "status": "parsing", "extra": "x" * 100_000}]}
    atomic_json(path / "state.json", state)
    calls = []
    def counted(file):
        calls.append(file)
        return read_json(file)
    monkeypatch.setattr(inventory, "read_json", counted)
    first = inventory.task_runs(output)[0]
    assert len(first["scheduled_stages"]) == expected
    assert first["stages"]["ingest"]["parsed_parts"] == 3
    assert first["usage"] == {"generation": {"calls": 14}}
    assert first["source_library"]["sources"] == [{"name": "source.txt", "status": "parsing"}]
    assert len(calls) == 2
    assert inventory.task_runs(output)[0] == first and len(calls) == 2
