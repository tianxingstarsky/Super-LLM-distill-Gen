"""Human augmentation is a persistent workspace, with explicit offline rounds.

UI actions prepare work at the application boundary. These tests never start
a worker process or call a model API.
"""
from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path
import json
import re

import pytest
from streamlit.testing.v1 import AppTest
import yaml


WORKSPACE = "human-workspace-ui"
SESSION_ID = "1" * 32
CHINESE = re.compile(r"[\u4e00-\u9fff]")


class StaticMarkupText(HTMLParser):
    """Inspect labels while respecting the UI's user-content boundary."""

    def __init__(self):
        super().__init__()
        self.skipped = []
        self.text = []

    def handle_starttag(self, tag, attrs):
        self.skipped.append(bool(self.skipped and self.skipped[-1]) or tag in {"style", "script"}
                            or "data-user-content" in dict(attrs))

    def handle_endtag(self, tag):
        if self.skipped:
            self.skipped.pop()

    def handle_data(self, value):
        if not (self.skipped and self.skipped[-1]) and value.strip():
            self.text.append(value.strip())


def write_service(root):
    path = root / "configs" / "backends.local.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({
        "default_backend": "offline", "default_model": "writer",
        "backends": {"offline": {"base_url": "https://example.invalid/v1", "api_key": "fixture-only",
                                    "models": ["writer"], "api_format": "chat"}},
    }), encoding="utf-8")
    (path.parent / "preferences.yaml").write_text(
        (Path(__file__).resolve().parents[1] / "configs" / "preferences.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )


def entry_screen(root):
    from pathlib import Path
    from unittest.mock import patch
    import streamlit as st

    from lib.application.backend_service import BackendApplication
    from lib.application.workflow_node_models_service import WorkflowNodeModelsApplication
    from lib.bootstrap.creation_drafts import creation_draft_application
    from lib.infrastructure.backend_config_driver import FilesystemBackendConfigDriver
    from lib.presentation.streamlit import workflow_page
    from lib.presentation.streamlit.i18n import install_streamlit_localization

    install_streamlit_localization()
    st.session_state["ws"] = "human-workspace-ui"
    for key in ("fixture-session-calls", "fixture-workflow-calls", "fixture-begin-calls"):
        st.session_state.setdefault(key, [])

    class Sources:
        def default_sft_output_style(self): return "separated"
        def source_files(self, *args, **kwargs): return []
        def task_runs(self): return []
        def agent_replay_capabilities(self): return {}
        def web_research_capabilities(self): return {"brave_configured": False}
        def create_run(self, **recipe):
            st.session_state["fixture-workflow-calls"].append(recipe)
            return "2" * 32

    class HumanWorkspace:
        def create_session(self, **recipe):
            st.session_state["fixture-session-calls"].append(recipe)
            return "1" * 32

    def begin(command):
        st.session_state["fixture-begin-calls"].append(command)

    backend = BackendApplication(FilesystemBackendConfigDriver(Path(root)))
    with patch.object(workflow_page, "render_canvas", lambda *args, **kwargs: None):
        workflow_page.render_workbench(
            Sources(), begin, WorkflowNodeModelsApplication(backend),
            backend_application=backend, draft_application=creation_draft_application(Path(root) / "output"),
            human_application=HumanWorkspace(),
        )


def entry(root, language="zh"):
    from lib.bootstrap.creation_drafts import creation_draft_application

    write_service(root)
    binding = {"backend": "offline", "model": "writer", "context_window_tokens": 131_072,
               "max_output_tokens": 32_768}
    creation_draft_application(root / "output").replace({
        f"workflow-preset:{WORKSPACE}": "自选目标",
        f"workflow-targets:{WORKSPACE}:自选目标": ["sft"],
        f"workflow-source-mode:{WORKSPACE}": "开放需求",
        f"workflow-node-bindings:{WORKSPACE}": {
            "ingest": {"generation": dict(binding)},
            "director": {"generation": dict(binding)},
            "sft": {"generation": dict(binding), "jev": dict(binding)}},
    })
    ui = AppTest.from_function(entry_screen, args=(str(root),), default_timeout=15)
    ui.session_state["ui_language"] = language
    ui.run()
    assert not ui.exception
    ui.segmented_control(key=f"workflow-creation-mode:{WORKSPACE}").set_value("人工问答增强").run()
    assert not ui.exception
    return ui


@pytest.mark.parametrize("language", ["zh", "en"])
def test_start_opens_human_workspace_without_requiring_designs_or_starting_a_model(tmp_path, language):
    ui = entry(tmp_path, language)
    assert ui.text_area(key=f"workflow-open-brief:{WORKSPACE}").value == ""
    assert not any(item.key and item.key.startswith("human-seed:") for item in ui.text_area)
    start = ui.button(key=f"workflow-create:{WORKSPACE}")
    assert start.label == ("Start human augmentation workspace" if language == "en" else "启动人工增强窗口")
    assert not start.disabled
    limit = ui.number_input(key=f"human-revision-limit:{WORKSPACE}")
    assert limit.min == 1 and limit.max == 10
    if language == "en":
        assert limit.label == "Maximum revisions for one result"
        assert any(item.proto.popover.label == "Revision limits" for item in ui.get("popover"))
        assert any("Each revision needs your submission." in item.value for item in ui.caption)
    limit.set_value(7).run()
    ui.button(key=f"workflow-create:{WORKSPACE}").click().run()
    assert not ui.exception
    assert len(ui.session_state["fixture-session-calls"]) == 1
    assert ui.session_state["fixture-workflow-calls"] == []
    assert ui.session_state["fixture-begin-calls"] == []
    assert ui.session_state["human-open-session"] == {"workspace": WORKSPACE, "session_id": SESSION_ID}
    assert "workflow-open-run" not in ui.session_state
    assert ui.session_state["fixture-session-calls"][0]["brief"] == ""
    assert ui.session_state["fixture-session-calls"][0]["max_revision_depth"] == 7


def test_director_node_does_not_contain_the_human_question_and_answer_editor(tmp_path):
    ui = entry(tmp_path)
    ui.session_state[f"workflow-setup-node:{WORKSPACE}"] = "director"
    ui.session_state[f"canvas-open:setup-canvas:{WORKSPACE}"] = True
    ui.run()
    assert not ui.exception
    assert ui.selectbox(key=f"workflow-director-mode:{WORKSPACE}").value == "adaptive"
    assert not any(item.key and item.key.startswith("human-seed:") for item in ui.text_area)
    assert not ui.get("toggle") or not any(item.key == f"workflow-human-enabled:{WORKSPACE}" for item in ui.toggle)


def create_workspace(root, *, name="Human QA designs", draft=None):
    from lib.bootstrap.workflows import human_augmentation_application

    write_service(root)
    binding = {"backend": "offline", "model": "writer", "context_window_tokens": 131_072,
               "max_output_tokens": 32_768}
    application = human_augmentation_application(root, root / "output")
    session_id = application.create_session(
        name=name, targets=["sft"], sources=[], brief="", sft_output_style="separated",
        node_models={"director": {"generation": dict(binding)},
                     "sft": {"generation": dict(binding), "jev": dict(binding)}},
    )
    if draft is not None:
        application.save_draft(session_id, draft, expected_version=application.session(session_id)["version"])
    return session_id


def studio_screen(root, session_id):
    from pathlib import Path
    from unittest.mock import patch
    import streamlit as st

    from lib.bootstrap.workflows import human_augmentation_application, workflow_application
    from lib.infrastructure.training_workflow import run_path, read_json
    from lib.infrastructure.workflow_driver import FilesystemWorkflowDriver
    from lib.io_utils import atomic_json
    from lib.presentation.streamlit import human_workspace_page
    from lib.presentation.streamlit.i18n import install_streamlit_localization

    install_streamlit_localization()
    st.session_state["ws"] = "human-workspace-ui"
    st.session_state.setdefault("fixture-begin-calls", [])
    root, output = Path(root), Path(root) / "output"

    def active(driver, run_id):
        # The fixture emulates an offline running worker through persisted state.
        # It never acquires a real execution lock or starts a subprocess.
        return read_json(run_path(driver.output, run_id) / "state.json")["status"] == "running"

    def begin(command):
        st.session_state["fixture-begin-calls"].append(command)
        file = run_path(output, command[-1]) / "state.json"
        state = read_json(file)
        state["status"] = "running"
        state["stages"]["director"]["status"] = "running"
        atomic_json(file, state)

    with patch.object(FilesystemWorkflowDriver, "is_active", active), \
            patch.object(human_workspace_page, "render_canvas", lambda *args, **kwargs: None):
        human_workspace_page.render_human_workspace(
            human_augmentation_application(root, output), workflow_application(root, output),
            begin, "human-workspace-ui", session_id=session_id,
        )


def studio(root, session_id, *, language="zh"):
    ui = AppTest.from_function(studio_screen, args=(str(root), session_id), default_timeout=15)
    ui.session_state["ui_language"] = language
    ui.run()
    assert not ui.exception
    return ui


def context(session_id):
    return f"human:{WORKSPACE}:{session_id}"


def design_key(session_id, index, field):
    return f"human-seed:{context(session_id)}:{index}:{field}"


def set_design(ui, session_id, index, question, answer):
    ui.text_area(key=design_key(session_id, index, "question")).set_value(question).run()
    ui.text_area(key=design_key(session_id, index, "answer")).set_value(answer).run()
    assert not ui.exception


def saved_workspace(root, session_id):
    from lib.bootstrap.workflows import human_augmentation_application

    return human_augmentation_application(root, root / "output").session(session_id)


def complete_round(root, session_id, *, question="When should the pump stop?", answer="Stop if its seal leaks.", count=1):
    from lib.infrastructure.training_workflow import run_path, read_json, file_hash
    from lib.io_utils import atomic_json

    session = saved_workspace(root, session_id)
    round_row = session["rounds"][-1]
    run = run_path(root / "output", round_row["run_id"])
    recipe = read_json(run / "recipe.json")
    design = deepcopy(recipe["qa_director"]["human_augmentation"])
    records = []
    for index in range(count):
        records.append({"id": f"candidate-{index}", "source_id": recipe["sources"][0]["sha256"], "status": "eligible",
            "evidence_level": "human_provided_and_model_assessed",
            "messages": [{"role": "user", "content": question + (f" {index}" if count > 1 else "")},
                         {"role": "assistant", "content": answer, "reasoning_content": "The stop condition protects the equipment."}],
            "qa_contract": {"human_design": {"seed": deepcopy(design["seeds"][0]),
                "question_requirements": design["question_requirements"],
                "answer_requirements": design["answer_requirements"]}},
        })
    artifacts = run / "artifacts"
    artifacts.mkdir(exist_ok=True)
    atomic_json(artifacts / "sft.records.json", records)
    (artifacts / "sft.jsonl").write_text("".join(json.dumps({"messages": row["messages"]}) + "\n" for row in records), encoding="utf-8")
    atomic_json(artifacts / "quality.json", {"targets": {"sft": {"total": count, "eligible": count}}})
    atomic_json(artifacts / "manifest.json", {"status": "complete", "counts": {"sft": count},
        "sha256": {file.name: file_hash(file) for file in artifacts.iterdir() if file.name != "manifest.json"}})
    state = read_json(run / "state.json")
    state["status"] = "completed"
    for stage in state["stages"].values():
        stage["status"] = "completed"
    atomic_json(run / "state.json", state)
    return round_row, records


def test_workspace_edits_switch_designs_and_restore_after_reload(tmp_path):
    session_id = create_workspace(tmp_path)
    ui = studio(tmp_path, session_id)
    set_design(ui, session_id, 0, "When should the pump stop?", "Stop if its seal leaks.")
    ui.text_area(key=design_key(session_id, 0, "answer_requirements")).set_value("Keep the stop condition.").run()
    ui.button(key=f"human-seed-add:{context(session_id)}").click().run()
    set_design(ui, session_id, 1, "When can it restart?", "After repair and inspection.")
    ui.selectbox(key=f"human-seed-selection:{context(session_id)}").set_value(0).run()
    assert ui.text_area(key=design_key(session_id, 0, "answer_requirements")).value == "Keep the stop condition."
    ui.text_area(key=f"workflow-human-question-requirements:{context(session_id)}").set_value("Use everyday words.").run()
    stored = saved_workspace(tmp_path, session_id)
    assert len(stored["draft"]["seeds"]) == 2
    assert stored["draft"]["question_requirements"] == "Use everyday words."
    assert ui.session_state["fixture-begin-calls"] == []
    fresh = studio(tmp_path, session_id)
    assert fresh.text_area(key=design_key(session_id, 0, "question")).value == "When should the pump stop?"
    fresh.selectbox(key=f"human-seed-selection:{context(session_id)}").set_value(1).run()
    assert fresh.text_area(key=design_key(session_id, 1, "answer")).value == "After repair and inspection."


@pytest.mark.parametrize("kind", ["json", "jsonl"])
def test_workspace_import_saves_clean_designs_and_recovers_them_in_a_new_session(tmp_path, kind):
    session_id = create_workspace(tmp_path)
    ui = studio(tmp_path, session_id)
    seeds = [{"question": "What does the alarm mean?", "answer": "The guard is open."},
             {"question": "What should I do next?", "answer": "Stop and inspect the guard."}]
    raw = json.dumps(seeds) if kind == "json" else "\n".join(json.dumps(seed) for seed in seeds)
    ui.text_area(key=f"human-seed-json:{context(session_id)}").set_value(raw).run()
    ui.button(key=f"human-seed-import:{context(session_id)}").click().run()
    assert not ui.exception
    assert not ui.error
    stored = saved_workspace(tmp_path, session_id)
    assert len(stored["draft"]["seeds"]) == 2
    assert all("id" not in seed for seed in stored["draft"]["seeds"])
    fresh = studio(tmp_path, session_id)
    assert fresh.text_area(key=design_key(session_id, 0, "question")).value == seeds[0]["question"]


def test_generation_persists_one_round_and_blocks_duplicate_clicks_and_reload(tmp_path):
    session_id = create_workspace(tmp_path)
    ui = studio(tmp_path, session_id)
    set_design(ui, session_id, 0, "When should the pump stop?", "Stop if its seal leaks.")
    ui.button(key=f"human-generate:{context(session_id)}").click().run()
    assert not ui.exception
    assert not ui.error
    stored = saved_workspace(tmp_path, session_id)
    assert len(stored["rounds"]) == 1
    run_id = stored["rounds"][0]["run_id"]
    assert ui.session_state["fixture-begin-calls"] == [["workflow", "--action", "resume", "--run-id", run_id]]
    assert ui.button(key=f"human-generate:{context(session_id)}").disabled
    from streamlit.testing.v1.errors import AppTestError
    with pytest.raises(AppTestError, match="disabled button"):
        ui.button(key=f"human-generate:{context(session_id)}").click()
    ui.run()
    assert not ui.exception
    assert len(saved_workspace(tmp_path, session_id)["rounds"]) == 1
    assert len(ui.session_state["fixture-begin-calls"]) == 1
    fresh = studio(tmp_path, session_id)
    assert fresh.button(key=f"human-generate:{context(session_id)}").disabled
    assert fresh.session_state["fixture-begin-calls"] == []


def test_resume_and_stop_keep_the_editor_version_current_for_the_next_round(tmp_path):
    from lib.bootstrap.workflows import human_augmentation_application
    from lib.infrastructure.training_workflow import read_json, run_path
    from lib.io_utils import atomic_json

    session_id = create_workspace(tmp_path, draft={"enabled": True, "seeds": [
        {"question": "When should the pump stop?", "answer": "Stop if its seal leaks."}]})
    application = human_augmentation_application(tmp_path, tmp_path / "output")
    prepared = application.generate_round(session_id, request_id="offline-prepared",
        expected_version=application.session(session_id)["version"], sample_count=1)
    ui = studio(tmp_path, session_id)
    ui.button(key=f"human-resume:{context(session_id)}").click().run()
    assert not ui.exception and not ui.error
    assert ui.session_state[f"human-version:{context(session_id)}"] == application.session(session_id)["version"]
    ui.button(key=f"human-stop:{context(session_id)}").click().run()
    assert not ui.exception and not ui.error
    assert ui.session_state[f"human-version:{context(session_id)}"] == application.session(session_id)["version"]
    ui.text_area(key=design_key(session_id, 0, "question")).set_value("The seal leaks. What should I do?").run()
    assert not ui.error
    # The owned fixture worker has now acknowledged the stop request.
    state_file = run_path(tmp_path / "output", prepared["run_id"]) / "state.json"
    state = read_json(state_file)
    state["status"] = "cancelled"
    atomic_json(state_file, state)
    ui.run()
    ui.button(key=f"human-generate:{context(session_id)}").click().run()
    assert not ui.exception and not ui.error
    assert len(application.session(session_id)["rounds"]) == 2


def text_area(ui, label):
    return next(item for item in ui.text_area if item.label == label)


def button(ui, label):
    return next(item for item in ui.button if item.label == label)


def test_selected_result_feedback_creates_a_new_review_round_and_keeps_previous_version(tmp_path):
    from lib.infrastructure.training_workflow import read_json, run_path

    session_id = create_workspace(tmp_path)
    ui = studio(tmp_path, session_id)
    set_design(ui, session_id, 0, "When should the pump stop?", "Stop if its seal leaks.")
    ui.button(key=f"human-generate:{context(session_id)}").click().run()
    assert not ui.exception
    first_round, originals = complete_round(tmp_path, session_id)
    parent_manifest = run_path(tmp_path / "output", first_round["run_id"]) / "artifacts" / "manifest.json"
    before = parent_manifest.read_bytes()
    ui.run()
    assert not ui.exception
    text_area(ui, "修正意见").set_value("Explain the action first and keep the stop condition.")
    text_area(ui, "修订问题").set_value("The pump seal is leaking. What should I do?")
    text_area(ui, "修订参考答案").set_value("Stop the pump, then inspect and repair the seal.")
    button(ui, "提交意见并回流修正").click().run()
    assert not ui.exception
    assert not ui.error
    stored = saved_workspace(tmp_path, session_id)
    assert len(stored["rounds"]) == 2
    revision = stored["rounds"][-1]
    assert revision["kind"] == "revision"
    assert revision["parent_results"][0]["candidate_id"] == originals[0]["id"]
    assert stored["feedback"][0]["applied_round_id"] == revision["id"]
    assert revision["feedback_ids"] == [stored["feedback"][0]["id"]]
    child = read_json(run_path(tmp_path / "output", revision["run_id"]) / "recipe.json")
    seed = child["qa_director"]["human_augmentation"]["seeds"][0]
    assert seed["question"] == "The pump seal is leaking. What should I do?"
    assert seed["answer"] == "Stop the pump, then inspect and repair the seal."
    assert seed["revision_context"]["instruction"] == "Explain the action first and keep the stop condition."
    assert seed["revision_context"]["messages"] == originals[0]["messages"]
    assert len(ui.session_state["fixture-begin-calls"]) == 2
    assert "workflow-open-run" not in ui.session_state
    assert parent_manifest.read_bytes() == before
    complete_round(tmp_path, session_id, question=seed["question"], answer=seed["answer"])
    ui.run()
    assert not ui.exception
    assert any("上一个版本仍保留" in item.value for item in ui.caption)
    ui.selectbox(key=f"human-round:{context(session_id)}").set_value(first_round["id"]).run()
    assert not ui.exception
    assert any(item.label == "此结果的人工意见记录" for item in ui.expander)
    assert any("Explain the action first and keep the stop condition." in item.value for item in ui.get("json"))
    fresh = studio(tmp_path, session_id)
    assert len(saved_workspace(tmp_path, session_id)["rounds"]) == 2
    assert fresh.selectbox(key=f"human-round:{context(session_id)}").value == revision["id"]
    assert fresh.session_state["fixture-begin-calls"] == []


def test_results_are_paged_without_losing_the_selected_round(tmp_path):
    session_id = create_workspace(tmp_path)
    ui = studio(tmp_path, session_id)
    set_design(ui, session_id, 0, "When should the pump stop?", "Stop if its seal leaks.")
    ui.button(key=f"human-generate:{context(session_id)}").click().run()
    round_row, _ = complete_round(tmp_path, session_id, count=12)
    ui.run()
    selected_key = f"human-result:{context(session_id)}:{round_row['id']}:sft"
    assert len(ui.selectbox(key=selected_key + ":0").options) == 10
    ui.button(key=f"human-next:{context(session_id)}:{round_row['id']}:sft").click().run()
    assert not ui.exception
    assert len(ui.selectbox(key=selected_key + ":10").options) == 2
    assert ui.button(key=f"human-next:{context(session_id)}:{round_row['id']}:sft").disabled
    assert ui.selectbox(key=f"human-round:{context(session_id)}").value == round_row["id"]
    ui.button(key=f"human-prev:{context(session_id)}:{round_row['id']}:sft").click().run()
    assert ui.selectbox(key=selected_key + ":0").value == "candidate-0"


def test_conflicting_draft_save_preserves_input_and_refresh_allows_retry(tmp_path):
    from lib.bootstrap.workflows import human_augmentation_application

    session_id = create_workspace(tmp_path)
    ui = studio(tmp_path, session_id)
    application = human_augmentation_application(tmp_path, tmp_path / "output")
    current = application.session(session_id)
    application.save_draft(session_id, {"enabled": True, "seeds": [
        {"question": "Remote question", "answer": "Remote answer"}]}, expected_version=current["version"])
    ui.text_area(key=design_key(session_id, 0, "question")).set_value("Local question").run()
    assert not ui.exception
    assert any("另一个窗口更新" in item.value for item in ui.error)
    assert ui.text_area(key=design_key(session_id, 0, "question")).value == "Local question"
    assert application.session(session_id)["draft"]["seeds"][0]["question"] == "Remote question"
    assert ui.button(key=f"human-generate:{context(session_id)}").disabled
    ui.button(key=f"human-refresh:{context(session_id)}").click().run()
    assert ui.text_area(key=design_key(session_id, 0, "question")).value == "Local question"
    ui.text_area(key=design_key(session_id, 0, "answer")).set_value("Local answer").run()
    assert not ui.error
    assert application.session(session_id)["draft"]["seeds"][0] == {
        "question": "Local question", "answer": "Local answer", "question_requirements": "", "answer_requirements": ""}


@pytest.mark.parametrize("with_results", [False, True])
def test_english_workspace_static_controls_and_help_have_no_translation_leaks(tmp_path, with_results):
    session_id = create_workspace(tmp_path, name="泵站问答设计", draft={"enabled": True, "seeds": [
        {"question": "When should the pump stop?", "answer": "Stop if its seal leaks."}]})
    ui = studio(tmp_path, session_id, language="en")
    if with_results:
        ui.button(key=f"human-generate:{context(session_id)}").click().run()
        complete_round(tmp_path, session_id)
        ui.run()
    assert not ui.exception
    labels = [item.label for kind in ("button", "text_area", "number_input", "selectbox", "expander", "segmented_control")
              for item in ui.get(kind)]
    labels += [item.proto.popover.label for item in ui.get("popover")]
    captions = [item.value for kind in ("caption", "info", "error", "warning") for item in ui.get(kind)]
    help_texts = [item.proto.help for kind in ("button", "text_area", "number_input", "selectbox") for item in ui.get(kind)]
    assert "Generate this round" in labels
    if with_results:
        assert "Result version" in labels
        assert "Submit feedback and generate a revision" in labels
    markup = [item.proto.body for item in ui.get("html")
              if any(css in item.proto.body for css in ("df-human-masthead", "df-human-loop", "df-human-empty", "df-human-result"))]
    assert ui.selectbox(key=f"human-history:{session_id}").options == ["泵站问答设计"]
    static_markup = StaticMarkupText()
    for value in markup:
        static_markup.feed(value)
    assert "Choose a result → Add feedback → Generate and review again → View the new version" in static_markup.text
    assert not any(CHINESE.search(value) for value in labels + captions + help_texts + static_markup.text)
