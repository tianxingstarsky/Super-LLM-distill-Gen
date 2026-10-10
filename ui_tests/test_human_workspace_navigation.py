"""Real console exits preserve human work and never start or cancel workers."""
from copy import deepcopy
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest
import yaml

from lib.presentation.streamlit.app_navigation import select_console_page


ROOT = Path(__file__).resolve().parents[1]


def test_explicit_exit_clears_only_navigation_and_the_entry_mode():
    state = {"nav": "人工问答增强", "ws": "default",
             "human-open-session": {"workspace": "default", "session_id": "old"},
             "human-selected:default": "current", "human-version:human:default:current": 4,
             "job:default": object(), "workflow-human-seeds:default": [{"question": "Q", "answer": "A"}],
             "workflow-form-draft:default": {"workflow-name:default": "Existing draft",
                                              "workflow-creation-mode:default": "人工问答增强"}}
    worker = state["job:default"]
    query = {"page": "人工问答增强", "human": "current", "lang": "en"}
    changes = select_console_page(state, query, "自动工作流")
    assert changes == {"workflow-creation-mode:default": "自动生成", "workflow-human-enabled:default": False}
    assert query == {"page": "自动工作流", "lang": "en"}
    assert "human-open-session" not in state
    assert state["human-selected:default"] == "current"
    assert state["human-version:human:default:current"] == 4
    assert state["job:default"] is worker
    assert state["workflow-human-seeds:default"] == [{"question": "Q", "answer": "A"}]
    assert state["workflow-form-draft:default"]["workflow-name:default"] == "Existing draft"


def test_navigation_preserves_an_intentional_human_entry_and_syncs_legacy_query():
    state = {"nav": "自动工作流", "workflow-creation-mode:default": "人工问答增强"}
    query = {"page": "模型与密钥"}
    assert select_console_page(state, query, "总览") == {}
    assert query["page"] == "总览"
    assert state["workflow-creation-mode:default"] == "人工问答增强"
    state["human-selected:default"] = "saved"
    query["human"] = "saved"
    assert select_console_page(state, query, "人工问答增强") == {}
    assert query["human"] == "saved"


@pytest.fixture
def isolated_console(tmp_path, monkeypatch):
    from lib import workspace as ws
    from lib.application.backend_service import BackendApplication
    from lib.bootstrap import backends, workflow_node_models, workflows
    from lib.bootstrap.creation_drafts import creation_draft_application
    from lib.console_jobs import Job
    from lib.infrastructure.backend_config_driver import FilesystemBackendConfigDriver
    from lib.infrastructure.training_workflow import read_json, run_path
    from lib.infrastructure.workflow_driver import FilesystemWorkflowDriver
    from lib.io_utils import atomic_json

    monkeypatch.delenv("DF_WORKSPACE", raising=False)
    monkeypatch.setattr(ws, "ROOT", tmp_path)
    monkeypatch.setattr(ws, "SEEDS_DIR", tmp_path / "data/seeds")
    monkeypatch.setattr(ws, "REGISTRY_PATH", tmp_path / "registry.json")
    monkeypatch.setattr(ws, "WORKSPACES_DIR", tmp_path / "legacy")
    monkeypatch.setattr(ws, "CURRENT_PATH", tmp_path / "current.json")
    ws.SEEDS_DIR.mkdir(parents=True)
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "backends.local.yaml").write_text(yaml.safe_dump({
        "default_backend": "offline", "default_model": "writer", "backends": {
            "offline": {"base_url": "https://example.invalid/v1", "api_key": "fixture-only",
                        "models": ["writer"], "api_format": "chat"}}}), encoding="utf-8")
    (configs / "preferences.yaml").write_text((ROOT / "configs/preferences.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    human_factory, workflow_factory = workflows.human_augmentation_application, workflows.workflow_application
    monkeypatch.setattr(workflows, "human_augmentation_application", lambda root, output: human_factory(tmp_path, output))
    monkeypatch.setattr(workflows, "workflow_application", lambda root, output: workflow_factory(tmp_path, output))
    backend_factory = lambda root: BackendApplication(FilesystemBackendConfigDriver(tmp_path))
    monkeypatch.setattr(backends, "backend_application", backend_factory)
    monkeypatch.setattr(workflow_node_models, "backend_application", backend_factory)

    def unexpected_worker(*args, **kwargs):
        raise AssertionError("Navigation must never start a model worker")

    monkeypatch.setattr(Job, "start", unexpected_worker)
    # Persisted state emulates a worker already in progress. These tests only
    # navigate; they never create an execution lock or call a model.
    monkeypatch.setattr(FilesystemWorkflowDriver, "is_active", lambda driver, run_id:
                        read_json(run_path(driver.output, run_id) / "state.json")["status"] == "running")
    output = ws.out("default")
    human = human_factory(tmp_path, output)
    binding = {"backend": "offline", "model": "writer", "context_window_tokens": 131_072, "max_output_tokens": 32_768}
    design = {"enabled": True, "seeds": [{"question": "When should the pump stop?", "answer": "Stop if its seal leaks."}]}
    session_id = human.create_session(name="Saved pump designs", sources=[], brief="", targets=["sft"],
        initial_draft=design, node_models={"director": {"generation": deepcopy(binding)},
            "sft": {"generation": deepcopy(binding), "jev": deepcopy(binding)}})
    session = human.session(session_id)
    round_row = human.generate_round(session_id, request_id="navigation-fixture", expected_version=session["version"], sample_count=1)
    state_file = run_path(output, round_row["run_id"]) / "state.json"
    native_state = read_json(state_file)
    native_state["status"] = "running"
    native_state["stages"]["sft"]["status"] = "running"
    atomic_json(state_file, native_state)
    creation_draft_application(output).replace({"workflow-creation-mode:default": "人工问答增强",
        "workflow-human-enabled:default": True, "workflow-human-seeds:default": deepcopy(design["seeds"]),
        "workflow-name:default": "Preserved entry draft"})
    return {"root": tmp_path, "output": output, "human": human, "session_id": session_id,
            "run_id": round_row["run_id"], "state_file": state_file, "native_state": state_file.read_bytes()}


def open_studio(fixture):
    ui = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=20)
    ui.session_state["ws"] = "default"
    ui.session_state["ui_language"] = "zh"
    ui.query_params["page"] = "人工问答增强"
    ui.query_params["human"] = fixture["session_id"]
    ui.run()
    assert not ui.exception
    assert ui.session_state["nav"] == "人工问答增强"
    return ui


def assert_preserved(fixture, before):
    assert fixture["human"].session(fixture["session_id"])["draft"] == before["draft"]
    assert fixture["human"].session(fixture["session_id"])["rounds"] == before["rounds"]
    assert fixture["state_file"].read_bytes() == fixture["native_state"]


@pytest.mark.parametrize("route", ["总览", "自动工作流", "任务管理"])
def test_sidebar_exits_clear_human_location_and_preserve_running_work(isolated_console, route):
    from lib.bootstrap.creation_drafts import creation_draft_application

    fixture = isolated_console
    ui = open_studio(fixture)
    before = fixture["human"].session(fixture["session_id"])
    ui.sidebar.button(key=f"nav-button:{route}").click().run()
    assert not ui.exception
    assert ui.session_state["nav"] == route
    assert ui.query_params["page"] == [route]
    assert "human" not in ui.query_params
    assert "human-open-session" not in ui.session_state
    ui.run()
    assert not ui.exception
    assert ui.session_state["nav"] == route
    ui.sidebar.button(key="nav-button:自动工作流").click().run()
    assert not ui.exception
    assert ui.segmented_control(key="workflow-creation-mode:default").value == "自动生成"
    saved = creation_draft_application(fixture["output"]).load()
    assert saved["workflow-creation-mode:default"] == "自动生成"
    assert not saved["workflow-human-enabled:default"]
    assert saved["workflow-human-seeds:default"][0]["answer"] == "Stop if its seal leaks."
    fresh = AppTest.from_file(str(ROOT / "lib/webapp.py"), default_timeout=20)
    fresh.query_params["page"] = "自动工作流"
    fresh.run()
    assert not fresh.exception
    assert fresh.segmented_control(key="workflow-creation-mode:default").value == "自动生成"
    assert_preserved(fixture, before)


def test_visible_exit_saves_blurred_design_and_history_reopens_same_session(isolated_console):
    fixture = isolated_console
    ui = open_studio(fixture)
    context = f"human:default:{fixture['session_id']}"
    ui.text_area(key=f"human-seed:{context}:0:answer_requirements").set_value("Keep the stop condition.").run()
    assert not ui.exception
    before = fixture["human"].session(fixture["session_id"])
    assert before["draft"]["seeds"][0]["answer_requirements"] == "Keep the stop condition."
    ui.button(key=f"human-exit:{context}").click().run()
    assert not ui.exception
    assert ui.session_state["nav"] == "自动工作流"
    assert "human" not in ui.query_params
    assert ui.segmented_control(key="workflow-creation-mode:default").value == "自动生成"
    ui.sidebar.button(key="nav-button:任务管理").click().run()
    ui.segmented_control(key="task-view:default").set_value("人工增强工作").run()
    assert not ui.exception
    ui.button(key=f"human-history-open:{fixture['session_id']}").click().run()
    assert not ui.exception
    assert ui.session_state["nav"] == "人工问答增强"
    assert ui.query_params["human"] == [fixture["session_id"]]
    assert ui.text_area(key=f"human-seed:{context}:0:answer_requirements").value == "Keep the stop condition."
    assert_preserved(fixture, before)


def test_direct_sidebar_task_handoff_also_leaves_the_studio(isolated_console):
    fixture = isolated_console
    ui = open_studio(fixture)
    before = fixture["human"].session(fixture["session_id"])
    # This callback sets nav directly, so the final dispatch must also clean
    # a stale studio URL instead of relying only on _select_page callbacks.
    ui.sidebar.button(key=f"sidebar-task:default:{fixture['run_id']}").click().run()
    assert not ui.exception
    assert ui.session_state["nav"] == "任务管理"
    assert ui.query_params["page"] == ["任务管理"]
    assert "human" not in ui.query_params
    assert ui.session_state["task-center-run:default"] == fixture["run_id"]
    assert ui.session_state["workflow-creation-mode:default"] == "自动生成"
    assert_preserved(fixture, before)
