"""Review model confirmation stays on its node and prepares no paid work."""
from pathlib import Path
import sys

import pytest
from streamlit.testing.v1 import AppTest


sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_human_workspace_ui import complete_round, context, write_service


@pytest.fixture(scope="session", autouse=True)
def mock_llm_server():
    yield


def _historical_result(root, *, jev=False):
    from lib.bootstrap.workflows import human_augmentation_application
    from lib.infrastructure.training_workflow import run_path

    write_service(root)
    binding = {"backend": "offline", "model": "writer", "context_window_tokens": 131_072,
               "max_output_tokens": 32_768}
    models = {"director": {"generation": dict(binding)}, "sft": {"generation": dict(binding)}}
    if jev:
        models["sft"]["jev"] = dict(binding)
    application = human_augmentation_application(root, root / "output")
    source_session = application.create_session(
        name="历史资料与结果", targets=["sft"], sources=[], brief="", sft_output_style="separated",
        review_repair={"mode": "auto", "max_rounds": 2, "score_threshold": .8},
        package_review={"enabled": jev}, node_models=models,
        initial_draft={"enabled": True, "seeds": [
            {"question": "什么时候停止泵？", "answer": "密封泄漏时停止泵。"}]},
    )
    application.generate_round(source_session, request_id="offline-history",
                               expected_version=application.session(source_session)["version"])
    round_row, _ = complete_round(root, source_session,
                                 question="什么时候停止泵？", answer="密封泄漏时停止泵。")
    session_id = application.review_session(round_row["run_id"])
    assert "review" not in application.session(session_id)["blueprint"]["node_models"]
    parent = run_path(root / "output", round_row["run_id"])
    sealed = {file: file.read_bytes() for file in [parent / "recipe.json", *(parent / "artifacts").iterdir()]
              if file.is_file()}
    return application, session_id, round_row["run_id"], sealed


def model_screen(root, session_id):
    from pathlib import Path
    from unittest.mock import patch
    import streamlit as st

    from lib.application.backend_service import BackendApplication
    from lib.bootstrap.workflows import human_augmentation_application, workflow_application
    from lib.infrastructure.backend_config_driver import FilesystemBackendConfigDriver
    from lib.llm_client import ChatClient
    from lib.presentation.streamlit import human_workspace_page
    from lib.presentation.streamlit.i18n import install_streamlit_localization

    install_streamlit_localization()
    st.session_state["ws"] = "human-workspace-ui"
    st.session_state.setdefault("fixture-begin-calls", [])
    root, output = Path(root), Path(root) / "output"

    def begin(command):
        st.session_state["fixture-begin-calls"].append(command)

    with patch.object(human_workspace_page, "render_canvas", lambda *args, **kwargs: None), \
            patch.object(ChatClient, "chat", side_effect=AssertionError("model confirmation called a model")):
        human_workspace_page.render_human_workspace(
            human_augmentation_application(root, output), workflow_application(root, output),
            begin, "human-workspace-ui", session_id=session_id,
            backend_application=BackendApplication(FilesystemBackendConfigDriver(root)),
        )


def _model_ui(root, session_id, run_id, *, language="zh"):
    ui = AppTest.from_function(model_screen, args=(str(root), session_id), default_timeout=15)
    ui.session_state["ui_language"] = language
    ui.session_state[f"workflow-stage:{run_id}"] = "review"
    ui.run()
    assert not ui.exception and not ui.error, [item.value for item in ui.error]
    assert any(item.proto.popover.label == ("View selected node settings" if language == "en"
                                          else "查看所选节点配置") for item in ui.get("popover"))
    return ui


def _model_context(session_id):
    return "rework:" + context(session_id)


def _prefix(session_id, role="generation"):
    return f"node-model:{_model_context(session_id)}:review:{role}"


def _confirm(ui, session_id):
    return ui.button(key=f"node-model-confirm:{_model_context(session_id)}:review")


def _assert_sealed(sealed):
    assert all(file.read_bytes() == content for file, content in sealed.items())


def test_history_can_confirm_review_models_in_its_node_without_starting_a_round(tmp_path):
    from lib.bootstrap.workflows import human_augmentation_application

    application, sid, run_id, sealed = _historical_result(tmp_path)
    ui = _model_ui(tmp_path, sid, run_id)
    prefix = _prefix(sid)
    ui.number_input(key=prefix + ":context:offline:writer").set_value(196_608).run()
    ui.number_input(key=prefix + ":output:offline:writer").set_value(40_960).run()
    assert not ui.exception and not ui.error
    assert "review" not in application.session(sid)["blueprint"]["node_models"]
    assert not _confirm(ui, sid).disabled
    _confirm(ui, sid).click().run()
    assert not ui.exception and not ui.error
    assert ui.session_state["fixture-begin-calls"] == []
    assert _confirm(ui, sid).disabled
    fresh = human_augmentation_application(tmp_path, tmp_path / "output").session(sid)
    assert len(fresh["rounds"]) == 1 and fresh["review_only"]
    assert fresh["blueprint"]["node_models"]["review"]["generation"] == {
        "backend": "offline", "model": "writer", "context_window_tokens": 196_608,
        "max_output_tokens": 40_960,
    }
    assert fresh["blueprint"]["session_endpoint_pins"]["review"]["generation"]
    reopened = _model_ui(tmp_path, sid, run_id)
    assert reopened.number_input(key=prefix + ":output:offline:writer").value == 40_960
    assert _confirm(reopened, sid).disabled
    assert reopened.session_state["fixture-begin-calls"] == []
    _assert_sealed(sealed)


def test_jev_review_roles_can_reuse_the_same_model_and_remain_durable(tmp_path):
    from lib.bootstrap.workflows import human_augmentation_application

    application, sid, run_id, sealed = _historical_result(tmp_path, jev=True)
    ui = _model_ui(tmp_path, sid, run_id)
    ui.number_input(key=_prefix(sid) + ":output:offline:writer").set_value(40_960).run()
    ui.button(key=_prefix(sid, "jev") + ":reuse-generation").click().run()
    assert not ui.exception and not ui.error
    assert ui.number_input(key=_prefix(sid, "jev") + ":output:offline:writer").value == 40_960
    assert not _confirm(ui, sid).disabled
    _confirm(ui, sid).click().run()
    assert not ui.exception and not ui.error
    fresh = human_augmentation_application(tmp_path, tmp_path / "output").session(sid)
    review = fresh["blueprint"]["node_models"]["review"]
    assert set(review) == {"generation", "jev"}
    assert review["generation"] == review["jev"]
    assert set(fresh["blueprint"]["session_endpoint_pins"]["review"]) == {"generation", "jev"}
    assert len(fresh["rounds"]) == 1
    assert ui.session_state["fixture-begin-calls"] == []
    assert application.session(sid)["blueprint"]["package_review"]["enabled"]
    _assert_sealed(sealed)


def test_stale_confirmation_reports_a_conflict_without_marking_the_model_saved(tmp_path):
    application, sid, run_id, sealed = _historical_result(tmp_path)
    ui = _model_ui(tmp_path, sid, run_id, language="en")
    before = application.session(sid)
    application.save_draft(sid, before["draft"], expected_version=before["version"])
    _confirm(ui, sid).click().run()
    assert not ui.exception and ui.error
    assert not _confirm(ui, sid).disabled
    assert "review" not in application.session(sid)["blueprint"]["node_models"]
    assert ui.session_state["fixture-begin-calls"] == []
    assert all(not any("\u4e00" <= char <= "\u9fff" for char in item.value) for item in ui.error)
    assert "历史资料与结果" in ui.selectbox(key=f"human-history:{sid}").options
    model_context = _model_context(sid)
    labels = [item.label for group in (ui.selectbox, ui.number_input, ui.button) for item in group
              if item.key and (item.key.startswith(f"node-model:{model_context}:")
                               or item.key.startswith(f"node-model-confirm:{model_context}:"))]
    assert labels and all(not any("\u4e00" <= char <= "\u9fff" for char in value) for value in labels)
    _assert_sealed(sealed)
