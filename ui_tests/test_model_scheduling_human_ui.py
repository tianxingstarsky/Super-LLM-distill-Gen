"""Shared model limits and human QA editing through actual Streamlit widgets.

Service metadata and drafts use temporary local storage. Starting a workflow
is captured at the application boundary; no model request or job is launched.
"""
from copy import deepcopy
from pathlib import Path
import json
import re

import pytest
from streamlit.testing.v1 import AppTest
import yaml


WORKSPACE = "human-ui"
CHINESE = re.compile(r"[\u4e00-\u9fff]")
CREATE_KEY = f"workflow-create:{WORKSPACE}"
MODE_KEY = f"workflow-creation-mode:{WORKSPACE}"


def model_binding():
    return {"backend": "offline", "model": "writer",
            "context_window_tokens": 131_072, "max_output_tokens": 32_768}


def write_service(root):
    path = root / "configs" / "backends.local.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump({
        "default_backend": "offline", "default_model": "writer",
        "backends": {"offline": {"base_url": "https://example.invalid/v1", "api_key": "fixture-only",
                                    "models": ["writer"], "api_format": "chat"}},
    }), encoding="utf-8")


def scheduling_screen(root, language):
    import streamlit as st
    from pathlib import Path
    from lib.application.backend_service import BackendApplication
    from lib.infrastructure.backend_config_driver import FilesystemBackendConfigDriver
    from lib.presentation.streamlit.i18n import install_streamlit_localization
    from lib.presentation.streamlit.workflow_node_settings import render_node_models

    install_streamlit_localization()
    workspace = "human-ui"
    st.session_state["ui_language"] = language
    binding = {"backend": "offline", "model": "writer", "context_window_tokens": 131_072,
               "max_output_tokens": 32_768}
    bindings = st.session_state.setdefault(f"workflow-node-bindings:{workspace}", {
        "sft": {"generation": dict(binding), "jev": dict(binding)}})
    application = BackendApplication(FilesystemBackendConfigDriver(Path(root)))
    endpoints = {row["name"]: row for row in application.list_backends()["backends"]}
    render_node_models("sft", "开放需求", workspace, bindings, endpoints,
                       backend_application=application)


def scheduling_key(role, field):
    return f"node-model:{WORKSPACE}:sft:{role}:scheduling:offline:writer:{field}"


def test_model_limit_persists_and_refreshes_sibling_without_changing_node_bindings(tmp_path):
    from lib.application.backend_service import BackendApplication
    from lib.infrastructure.backend_config_driver import FilesystemBackendConfigDriver

    write_service(tmp_path)
    ui = AppTest.from_function(scheduling_screen, args=(str(tmp_path), "zh")).run()
    assert not ui.exception
    original = deepcopy(ui.session_state[f"workflow-node-bindings:{WORKSPACE}"])
    ui.number_input(key=scheduling_key("generation", "limit")).set_value(2).run()
    assert not ui.exception
    assert ui.number_input(key=scheduling_key("jev", "limit")).value == 2
    ui.number_input(key=scheduling_key("jev", "wait")).set_value(45).run()
    assert ui.number_input(key=scheduling_key("generation", "wait")).value == 45
    application = BackendApplication(FilesystemBackendConfigDriver(tmp_path))
    state = application.get_model_scheduling("offline", "writer")
    assert state["max_concurrency"] == 2
    assert state["request_queue_timeout_seconds"] == 45
    assert state["active"] == state["queued"] == 0
    assert ui.session_state[f"workflow-node-bindings:{WORKSPACE}"] == original
    draft = ui.session_state[f"workflow-form-draft:{WORKSPACE}"]
    assert draft[f"workflow-node-bindings:{WORKSPACE}"] == original
    assert "max_concurrency" not in json.dumps(draft)
    assert "request_queue_timeout_seconds" not in json.dumps(draft)
    fresh = AppTest.from_function(scheduling_screen, args=(str(tmp_path), "zh")).run()
    assert not fresh.exception
    assert fresh.number_input(key=scheduling_key("generation", "limit")).value == 2
    assert fresh.number_input(key=scheduling_key("jev", "wait")).value == 45


def test_failed_model_limit_save_restores_authority_for_both_roles(tmp_path, monkeypatch):
    from lib.application.backend_service import BackendApplication

    write_service(tmp_path)
    ui = AppTest.from_function(scheduling_screen, args=(str(tmp_path), "zh")).run()
    original = ui.number_input(key=scheduling_key("generation", "limit")).value
    def fail(*args, **kwargs):
        raise OSError("Controlled storage failure")
    monkeypatch.setattr(BackendApplication, "save_model_scheduling", fail)
    ui.number_input(key=scheduling_key("generation", "limit")).set_value(2).run()
    assert not ui.exception
    assert ui.number_input(key=scheduling_key("generation", "limit")).value == original
    assert ui.number_input(key=scheduling_key("jev", "limit")).value == original
    assert any("已恢复当前生效值" in item.value for item in ui.error)


def workbench_screen(root):
    import streamlit as st
    from pathlib import Path
    from unittest.mock import patch

    from lib.application.backend_service import BackendApplication
    from lib.application.workflow_node_models_service import WorkflowNodeModelsApplication
    from lib.bootstrap.creation_drafts import creation_draft_application
    from lib.infrastructure.backend_config_driver import FilesystemBackendConfigDriver
    from lib.presentation.streamlit import workflow_page
    from lib.presentation.streamlit.i18n import install_streamlit_localization

    install_streamlit_localization()
    workspace = "human-ui"
    st.session_state["ws"] = workspace
    st.session_state.setdefault("fixture-create-calls", [])
    st.session_state.setdefault("fixture-begin-calls", [])

    class Sources:
        def default_sft_output_style(self): return "separated"
        def source_files(self, *args, **kwargs): return []
        def task_runs(self): return []
        def agent_replay_capabilities(self): return {}
        def web_research_capabilities(self): return {"brave_configured": False}
        def create_run(self, **recipe):
            st.session_state["fixture-create-calls"].append(recipe)
            return "offline-created-run"

    backend = BackendApplication(FilesystemBackendConfigDriver(Path(root)))
    drafts = creation_draft_application(Path(root) / "output")
    def begin(command):
        st.session_state["fixture-begin-calls"].append(command)
    with patch.object(workflow_page, "render_canvas", lambda *args, **kwargs: None):
        workflow_page.render_workbench(Sources(), begin, WorkflowNodeModelsApplication(backend),
                                       backend_application=backend, draft_application=drafts)


def workbench(root, *, language="zh", human=False, seeds=None):
    from lib.bootstrap.creation_drafts import creation_draft_application

    write_service(root)
    bindings = {
        "ingest": {"generation": model_binding()},
        "director": {"generation": model_binding()},
        "sft": {"generation": model_binding(), "jev": model_binding()},
    }
    values = {
        f"workflow-preset:{WORKSPACE}": "自选目标",
        f"workflow-targets:{WORKSPACE}:自选目标": ["sft"],
        f"workflow-source-mode:{WORKSPACE}": "开放需求",
        f"workflow-node-bindings:{WORKSPACE}": bindings,
        f"workflow-director-enabled:{WORKSPACE}": human,
        f"workflow-human-enabled:{WORKSPACE}": human,
        MODE_KEY: "人工问答增强" if human else "自动生成",
    }
    if human:
        values[f"workflow-human-seeds:{WORKSPACE}"] = seeds or [{"question": "", "answer": ""}]
    creation_draft_application(root / "output").replace(values)
    ui = AppTest.from_function(workbench_screen, args=(str(root),), default_timeout=15)
    ui.session_state["ui_language"] = language
    if human:
        ui.session_state[f"workflow-setup-node:{WORKSPACE}"] = "director"
    ui.run()
    assert not ui.exception
    return ui


def enter_human(ui):
    ui.segmented_control(key=MODE_KEY).set_value("人工问答增强").run()
    assert not ui.exception
    assert ui.session_state[f"workflow-setup-node:{WORKSPACE}"] == "director"


def seed_key(index, field):
    return f"human-seed:{WORKSPACE}:{index}:{field}"


def set_design(ui, index, question, answer):
    ui.text_area(key=seed_key(index, "question")).set_value(question).run()
    ui.text_area(key=seed_key(index, "answer")).set_value(answer).run()
    assert not ui.exception


def test_human_entry_enables_director_opens_its_node_and_blocks_incomplete_design(tmp_path):
    ui = workbench(tmp_path)
    enter_human(ui)
    assert ui.toggle(key=f"workflow-director-enabled:{WORKSPACE}").value is True
    assert ui.toggle(key=f"workflow-human-enabled:{WORKSPACE}").value is True
    assert ui.session_state[f"canvas-open:setup-canvas:{WORKSPACE}"] is True
    assert ui.session_state[f"workflow-setup-reveal:{WORKSPACE}"]["node"] == "director"
    assert ui.selectbox(key=f"workflow-director-mode:{WORKSPACE}").value == "adaptive"
    assert ui.selectbox(key=f"workflow-director-mode:{WORKSPACE}").disabled
    assert ui.button(key=CREATE_KEY).disabled
    assert ui.session_state["fixture-create-calls"] == []
    assert ui.session_state["fixture-begin-calls"] == []


def test_human_mode_cannot_silently_run_as_plain_generation_with_director_disabled(tmp_path):
    ui = workbench(tmp_path)
    enter_human(ui)
    set_design(ui, 0, "What does a red light mean?", "Stop and check the alarm.")
    ui.text_area(key=f"workflow-open-brief:{WORKSPACE}").set_value("Keep the same safety conditions.").run()
    ui.toggle(key=f"workflow-director-enabled:{WORKSPACE}").set_value(False).run()
    assert not ui.exception
    assert ui.button(key=CREATE_KEY).disabled
    assert ui.session_state["fixture-create-calls"] == []


def test_human_design_edits_survive_seed_switch_mode_switch_and_fresh_session(tmp_path):
    from lib.bootstrap.creation_drafts import creation_draft_application

    ui = workbench(tmp_path)
    enter_human(ui)
    set_design(ui, 0, "When should the pump stop?", "Stop if its seal leaks.")
    ui.text_area(key=seed_key(0, "answer_requirements")).set_value("Keep the stop condition.").run()
    ui.button(key=f"human-seed-add:{WORKSPACE}").click().run()
    set_design(ui, 1, "When can it restart?", "After the repair and inspection.")
    ui.selectbox(key=f"human-seed-selection:{WORKSPACE}").set_value(0).run()
    assert ui.text_area(key=seed_key(0, "question")).value == "When should the pump stop?"
    assert ui.text_area(key=seed_key(0, "answer_requirements")).value == "Keep the stop condition."
    ui.text_area(key=f"workflow-human-question-requirements:{WORKSPACE}").set_value("Use everyday language.").run()
    ui.segmented_control(key=MODE_KEY).set_value("自动生成").run()
    assert not any(item.key and item.key.startswith("human-seed:") for item in ui.text_area)
    enter_human(ui)
    assert ui.text_area(key=seed_key(0, "question")).value == "When should the pump stop?"
    ui.selectbox(key=f"human-seed-selection:{WORKSPACE}").set_value(1).run()
    assert ui.text_area(key=seed_key(1, "answer")).value == "After the repair and inspection."
    stored = creation_draft_application(tmp_path / "output").load()
    assert len(stored[f"workflow-human-seeds:{WORKSPACE}"]) == 2
    assert stored[f"workflow-human-question-requirements:{WORKSPACE}"] == "Use everyday language."
    fresh = AppTest.from_function(workbench_screen, args=(str(tmp_path),), default_timeout=15)
    fresh.session_state[f"workflow-setup-node:{WORKSPACE}"] = "director"
    fresh.run()
    assert not fresh.exception
    assert fresh.segmented_control(key=MODE_KEY).value == "人工问答增强"
    assert fresh.text_area(key=seed_key(0, "question")).value == "When should the pump stop?"
    assert fresh.text_area(key=f"workflow-human-question-requirements:{WORKSPACE}").value == "Use everyday language."


@pytest.mark.parametrize("kind", ["json", "jsonl"])
def test_human_json_import_replaces_and_persists_designs_through_button_callback(tmp_path, kind):
    from lib.bootstrap.creation_drafts import creation_draft_application

    ui = workbench(tmp_path)
    enter_human(ui)
    seeds = [{"question": "What does the alarm mean?", "answer": "The guard is open.",
              "question_requirements": "Ask as a new operator.", "answer_requirements": "Keep the cause."},
             {"question": "What should I do next?", "answer": "Stop and inspect the guard."}]
    raw = json.dumps(seeds) if kind == "json" else "\n".join(json.dumps(row) for row in seeds)
    ui.text_area(key=f"human-seed-json:{WORKSPACE}").set_value(raw).run()
    ui.button(key=f"human-seed-import:{WORKSPACE}").click().run()
    assert not ui.exception
    assert ui.text_area(key=seed_key(0, "question")).value == seeds[0]["question"]
    assert ui.text_area(key=seed_key(0, "answer_requirements")).value == "Keep the cause."
    stored = creation_draft_application(tmp_path / "output").load()[f"workflow-human-seeds:{WORKSPACE}"]
    assert len(stored) == 2
    assert not any("id" in row for row in stored)
    ui.selectbox(key=f"human-seed-selection:{WORKSPACE}").set_value(1).run()
    assert ui.text_area(key=seed_key(1, "answer")).value == seeds[1]["answer"]
    assert f"human-seed-import-error:{WORKSPACE}" not in ui.session_state


@pytest.mark.parametrize("raw", ["broken JSON", '[{"question":"Missing answer"}]',
                                 '[{"question":"Q","answer":"A"},{"question":"Q","answer":"A"}]'])
def test_failed_human_import_keeps_current_question_and_answer(tmp_path, raw):
    ui = workbench(tmp_path)
    enter_human(ui)
    set_design(ui, 0, "When should the pump stop?", "Stop if its seal leaks.")
    prior = deepcopy(ui.session_state[f"workflow-form-draft:{WORKSPACE}"][f"workflow-human-seeds:{WORKSPACE}"])
    ui.text_area(key=f"human-seed-json:{WORKSPACE}").set_value(raw).run()
    ui.button(key=f"human-seed-import:{WORKSPACE}").click().run()
    assert not ui.exception
    assert ui.session_state[f"workflow-form-draft:{WORKSPACE}"][f"workflow-human-seeds:{WORKSPACE}"] == prior
    assert any("当前设计已保留" in item.value for item in ui.error)


def test_valid_human_question_and_answer_can_start_without_files_or_an_open_brief(tmp_path):
    ui = workbench(tmp_path)
    enter_human(ui)
    assert ui.text_area(key=f"workflow-open-brief:{WORKSPACE}").value == ""
    assert ui.button(key=CREATE_KEY).disabled
    ui.text_area(key=seed_key(0, "question")).set_value("When should the pump stop?").run()
    assert ui.button(key=CREATE_KEY).disabled
    ui.text_area(key=seed_key(0, "answer")).set_value("Stop if its seal leaks.").run()
    assert not ui.button(key=CREATE_KEY).disabled
    assert ui.session_state["fixture-create-calls"] == []
    ui.button(key=CREATE_KEY).click().run()
    assert not ui.exception
    recipes = ui.session_state["fixture-create-calls"]
    assert len(recipes) == 1
    assert recipes[0]["sources"] == []
    assert recipes[0]["brief"] == ""
    assert recipes[0]["qa_director"]["enabled"] is True
    assert recipes[0]["qa_director"]["human_augmentation"]["seeds"][0]["answer"] == "Stop if its seal leaks."
    assert "max_concurrency" not in json.dumps(recipes[0]["node_models"])
    assert ui.session_state["fixture-begin-calls"] == [["workflow", "--action", "resume", "--run-id", "offline-created-run"]]


def test_human_only_mode_does_not_require_an_unused_input_planning_model(tmp_path):
    ui = workbench(tmp_path)
    enter_human(ui)
    set_design(ui, 0, "When should the pump stop?", "Stop if its seal leaks.")
    bindings = deepcopy(ui.session_state[f"workflow-node-bindings:{WORKSPACE}"])
    bindings.pop("ingest", None)
    ui.session_state[f"workflow-node-bindings:{WORKSPACE}"] = bindings
    draft_key = f"workflow-form-draft:{WORKSPACE}"
    ui.session_state[draft_key] = {**ui.session_state[draft_key], f"workflow-node-bindings:{WORKSPACE}": bindings}
    ui.run()
    assert not ui.exception
    assert not ui.button(key=CREATE_KEY).disabled
    ui.button(key=CREATE_KEY).click().run()
    assert not ui.exception
    assert ui.session_state["fixture-create-calls"][0]["node_models"].get("ingest", {}) == {}


def test_english_human_entry_and_incomplete_design_hint_are_translated(tmp_path):
    ui = workbench(tmp_path, language="en")
    enter_human(ui)
    labels = [item.label for kind in ("button", "text_area", "number_input", "expander")
              for item in ui.get(kind)]
    captions = [item.value for item in ui.caption]
    assert "Edit human QA designs" in labels
    assert "Additional generation notes (optional)" in labels
    assert "Web sources (optional)" in labels
    assert "Complete the human question and reference answer on the director node first." in captions
    assert not any(CHINESE.search(text) for text in labels + captions)
    assert ui.button(key=CREATE_KEY).disabled


def human_controls_screen(language):
    import streamlit as st
    from copy import deepcopy
    from lib.presentation.streamlit.human_augmentation_controls import render_human_designs
    from lib.presentation.streamlit.i18n import install_streamlit_localization

    install_streamlit_localization()
    workspace = "human-ui"
    st.session_state["ui_language"] = language
    st.session_state.setdefault(f"workflow-form-draft:{workspace}", {
        f"workflow-human-enabled:{workspace}": True,
        f"workflow-human-seeds:{workspace}": [
            {"question": "Question one", "answer": "Answer one"},
            {"question": "Question two", "answer": "Answer two"}],
    })
    def save_field(workspace, key):
        current = st.session_state[f"workflow-form-draft:{workspace}"]
        st.session_state[f"workflow-form-draft:{workspace}"] = {**current, key: deepcopy(st.session_state[key])}
    render_human_designs(workspace, save_field=save_field)


def test_new_human_controls_render_english_labels_and_help_without_translation_leaks():
    ui = AppTest.from_function(human_controls_screen, args=("en",)).run()
    assert not ui.exception
    labels = [item.label for kind in ("toggle", "selectbox", "button", "text_area", "file_uploader", "expander")
              for item in ui.get(kind)]
    labels += [item.proto.popover.label for item in ui.get("popover")]
    captions = [item.value for item in ui.caption]
    help_texts = [item.proto.help for kind in ("toggle", "selectbox", "button", "text_area") for item in ui.get(kind)]
    assert "Human QA augmentation" in labels
    assert "Your question" in labels
    assert "Your reference answer" in labels
    assert "Apply import" in labels
    assert not any(CHINESE.search(text) for text in labels + captions + help_texts)


def test_shared_model_controls_render_english_labels_and_help_without_translation_leaks(tmp_path):
    write_service(tmp_path)
    ui = AppTest.from_function(scheduling_screen, args=(str(tmp_path), "en")).run()
    assert not ui.exception
    limits = [ui.number_input(key=scheduling_key(role, "limit")) for role in ("generation", "jev")]
    waits = [ui.number_input(key=scheduling_key(role, "wait")) for role in ("generation", "jev")]
    assert all(item.label == "Maximum concurrent requests for this model" for item in limits)
    assert all(item.label == "Maximum queue wait (seconds)" for item in waits)
    assert not any(CHINESE.search(item.proto.help) for item in limits + waits)
    assert any("Shared pool" in item.value for item in ui.caption)
