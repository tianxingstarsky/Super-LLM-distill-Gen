"""Launch readiness and source placement through the real Streamlit workbench.

Ports remain offline. The uploader return is the only widget boundary stubbed:
AppTest does not provide a file-upload interaction API.
"""
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest


WORKSPACE = "readiness"
DOCUMENTS = "文档资料"
AGENT = "Agent 上下文"
OPEN = "开放需求"
TARGETS_KEY = f"workflow-targets:{WORKSPACE}:自选目标"
CREATE_KEY = f"workflow-create:{WORKSPACE}"


def workbench_screen(source_paths, sizes, upload_name, vision_confirmed, with_preview):
    from contextlib import ExitStack
    from pathlib import Path
    from types import SimpleNamespace
    from unittest.mock import patch

    import streamlit as st

    from lib.application.workflow_node_models_service import WorkflowNodeModelsApplication
    from lib.presentation.streamlit import workflow_page

    workspace = "readiness"
    st.session_state.setdefault("ws", workspace)
    st.session_state.setdefault("fixture-create-calls", [])
    st.session_state.setdefault("fixture-begin-calls", [])

    class Sources:
        def default_sft_output_style(self): return "separated"
        def task_runs(self): return []
        def agent_replay_capabilities(self): return {}
        def web_research_capabilities(self): return {"brave_configured": False}
        def source_files(self, _workspace, *, suffixes, limit):
            return [{"path": path, "label": Path(path).name} for path in source_paths
                    if Path(path).suffix.lower() in suffixes][:limit]
        def create_run(self, **recipe):
            st.session_state["fixture-create-calls"].append(recipe)
            return "offline-created-run"

    class Inventory:
        def list_backends(self):
            return {"default_backend": "offline", "default_model": "writer", "roles": {},
                    "backends": [{"name": "offline", "models": ["writer"],
                                  "model_capabilities": {"writer": {"vision": vision_confirmed}}}]}

    class Preview:
        def signature(self, source, chunk_chars): return (source, chunk_chars)
        def preview(self, *_args): raise AssertionError("Preview is an explicit, unclicked action")
        def describe(self, source, _suffixes): return {"label": Path(source).name}

    def begin(args): st.session_state["fixture-begin-calls"].append(args)

    real_stat = Path.stat
    def source_stat(path, *args, **kwargs):
        if str(path) in sizes:
            return SimpleNamespace(st_size=sizes[str(path)])
        return real_stat(path, *args, **kwargs)

    real_uploader = st.file_uploader
    def source_uploader(*args, **kwargs):
        result = real_uploader(*args, **kwargs)
        if upload_name and kwargs.get("key") == f"workflow-upload:{workspace}:文档资料":
            return [SimpleNamespace(name=upload_name, size=8,
                                    getvalue=lambda: b"\x89PNG\r\n\x1a\n")]
        return result

    with ExitStack() as patches:
        if sizes:
            patches.enter_context(patch.object(Path, "stat", source_stat))
        if upload_name:
            patches.enter_context(patch.object(st, "file_uploader", source_uploader))
        workflow_page.render_workbench(
            Sources(), begin, WorkflowNodeModelsApplication(Inventory()),
            document_preview=Preview() if with_preview else None)


def workbench(source_paths=(), *, targets=("cpt",), selected=(), source_mode=DOCUMENTS,
              parser="native", sizes=None, upload_name=None, vision_confirmed=False,
              with_preview=False, extra_draft=None):
    app = AppTest.from_function(
        workbench_screen,
        args=([str(path) for path in source_paths], sizes or {}, upload_name,
              vision_confirmed, with_preview), default_timeout=15)
    app.session_state[f"workflow-form-draft:{WORKSPACE}"] = {
        f"workflow-preset:{WORKSPACE}": "自选目标",
        TARGETS_KEY: list(targets),
        f"workflow-source-mode:{WORKSPACE}": source_mode,
        f"workflow-sources:{WORKSPACE}:{source_mode}": [str(path) for path in selected],
        f"workflow-document-parse-mode:{WORKSPACE}": parser,
        f"workflow-cpt-processing-mode:{WORKSPACE}": "native",
        **(extra_draft or {}),
    }
    app.run()
    assert not app.exception
    return app


def source(tmp_path, name="guide.txt"):
    path = tmp_path / name
    path.write_text("Keep the machine disconnected during inspection.", encoding="utf-8")
    return path


def issue_html(app):
    return "\n".join(str(item.value) for item in app.get("html")
                     if 'class="df-source-issue"' in str(item.value))


def assert_no_run(app):
    assert not app.exception
    assert app.session_state["fixture-create-calls"] == []
    assert app.session_state["fixture-begin-calls"] == []


@pytest.mark.parametrize("mode", [DOCUMENTS, OPEN])
def test_missing_source_or_open_brief_disables_launch_before_create(mode):
    app = workbench(source_mode=mode)
    assert app.button(key=CREATE_KEY).disabled
    assert_no_run(app)
    # Refresh and inspect another node without supplying the missing input.
    app.session_state[f"workflow-setup-node:{WORKSPACE}"] = "package"
    app.run()
    assert app.button(key=CREATE_KEY).disabled
    assert_no_run(app)


def test_valid_native_cpt_source_can_launch_without_model_calls(tmp_path):
    document = source(tmp_path)
    app = workbench([document], selected=[document])
    assert not app.button(key=CREATE_KEY).disabled
    app.button(key=CREATE_KEY).click().run()
    assert not app.exception
    calls = app.session_state["fixture-create-calls"]
    assert len(calls) == 1
    assert calls[0]["targets"] == ["cpt"]
    assert calls[0]["sources"] == [document]
    assert calls[0]["document_parser"] == {"mode": "native"}
    assert calls[0]["cpt_processing"] == {"mode": "native"}
    assert calls[0]["node_models"] == {}
    assert app.session_state["fixture-begin-calls"] == [
        ["workflow", "--action", "resume", "--run-id", "offline-created-run"]]


@pytest.mark.parametrize("brief,expected", [
    ("Send results to training@example.com.", "个人信息"),
    ("Use sk-1234567890abcdefghijkl for examples.", "密钥"),
    ("Generate training examples with \ufffd characters.", "损坏字符"),
])
def test_invalid_optional_brief_links_back_to_source_and_blocks_launch(tmp_path, brief, expected):
    document = source(tmp_path)
    app = workbench([document], selected=[document])
    key = f"workflow-source-brief:{WORKSPACE}:{DOCUMENTS}"
    app.text_area(key=key).set_value(brief).run()
    assert app.button(key=CREATE_KEY).disabled
    assert 'href="#workflow-source-entry"' in issue_html(app)
    assert expected in issue_html(app)
    assert_no_run(app)
    app.text_area(key=key).set_value("").run()
    assert not app.button(key=CREATE_KEY).disabled
    assert_no_run(app)


def test_more_than_200_sources_is_blocked_and_the_boundary_is_allowed(tmp_path):
    documents = [source(tmp_path, f"guide-{index}.txt") for index in range(201)]
    app = workbench(documents, selected=documents)
    assert app.button(key=CREATE_KEY).disabled
    assert "200 份来源" in issue_html(app)
    assert 'href="#workflow-source-entry"' in issue_html(app)
    assert_no_run(app)
    app.multiselect(key=f"workflow-sources:{WORKSPACE}:{DOCUMENTS}").set_value(
        [str(path) for path in documents[:200]]).run()
    assert not app.button(key=CREATE_KEY).disabled
    assert_no_run(app)


def test_cumulative_source_bytes_are_checked_without_allocating_large_files(tmp_path):
    documents = [source(tmp_path, f"part-{index}.txt") for index in range(6)]
    sizes = {str(path): 40 * 1024 * 1024 for path in documents[:5]}
    sizes[str(documents[-1])] = 1
    app = workbench(documents, selected=documents, sizes=sizes)
    assert app.button(key=CREATE_KEY).disabled
    assert "超过 200 MiB" in issue_html(app)
    assert_no_run(app)
    app.multiselect(key=f"workflow-sources:{WORKSPACE}:{DOCUMENTS}").set_value(
        [str(path) for path in documents[:5]]).run()
    assert not app.button(key=CREATE_KEY).disabled
    assert_no_run(app)


def test_missing_selected_file_provides_actionable_source_issue(tmp_path):
    document = source(tmp_path)
    app = workbench([document], selected=[document])
    assert not app.button(key=CREATE_KEY).disabled
    document.unlink()
    app.run()
    assert app.button(key=CREATE_KEY).disabled
    assert "来源文件已失联" in issue_html(app)
    assert_no_run(app)


def test_agent_source_fix_switches_mode_and_preserves_each_source_draft(tmp_path):
    document = source(tmp_path)
    trace = source(tmp_path, "recorded.jsonl")
    brief_key = f"workflow-source-brief:{WORKSPACE}:{DOCUMENTS}"
    app = workbench([document, trace], targets=["agent"], selected=[document], extra_draft={
        brief_key: "Retain this document context for later.",
        f"workflow-sources:{WORKSPACE}:{AGENT}": [str(trace)],
    })
    assert app.button(key=CREATE_KEY).disabled
    app.button(key=f"workflow-agent-source-fix:{WORKSPACE}").click().run()
    assert not app.exception
    assert app.segmented_control(key=f"workflow-source-mode:{WORKSPACE}").value == AGENT
    assert app.pills(key=TARGETS_KEY).value == ["agent"]
    assert app.multiselect(key=f"workflow-sources:{WORKSPACE}:{AGENT}").value == [str(trace)]
    assert not app.button(key=CREATE_KEY).disabled
    draft = app.session_state[f"workflow-form-draft:{WORKSPACE}"]
    assert draft[f"workflow-sources:{WORKSPACE}:{DOCUMENTS}"] == [str(document)]
    assert draft[brief_key] == "Retain this document context for later."
    assert_no_run(app)
    app.segmented_control(key=f"workflow-source-mode:{WORKSPACE}").set_value(DOCUMENTS).run()
    assert app.multiselect(key=f"workflow-sources:{WORKSPACE}:{DOCUMENTS}").value == [str(document)]
    assert app.text_area(key=brief_key).value == draft[brief_key]
    assert app.button(key=CREATE_KEY).disabled
    assert_no_run(app)


@pytest.mark.parametrize("parser,confirmed,blocked", [
    ("native", False, True), ("model", False, True),
    ("vision", False, True), ("vision", True, False),
])
def test_direct_uploaded_image_requires_a_confirmed_vision_parser(parser, confirmed, blocked):
    app = workbench(parser=parser, upload_name="dropped-image.png", vision_confirmed=confirmed)
    assert not app.multiselect  # The dropped file is the only input.
    assert app.button(key=CREATE_KEY).disabled is blocked
    assert_no_run(app)


def test_source_target_canvas_and_production_controls_follow_reading_order(tmp_path):
    document = source(tmp_path)
    app = workbench([document], targets=["sft"], selected=[document])
    elements = list(app.main)
    def position(kind, key):
        return next(index for index, item in enumerate(elements)
                    if item.type == kind and getattr(item, "key", None) == key)
    source_position = position("file_uploader", f"workflow-upload:{WORKSPACE}:{DOCUMENTS}")
    targets_position = position("button_group", TARGETS_KEY)
    canvas_position = next(index for index, item in enumerate(elements)
                           if item.type == "component_instance")
    plan_position = position("text_input", f"workflow-name:{WORKSPACE}")
    assert source_position < targets_position < canvas_position < plan_position
    assert_no_run(app)


def test_node_switches_keep_one_source_and_one_copy_of_each_production_control(tmp_path):
    document = source(tmp_path)
    app = workbench([document], targets=["sft"], selected=[document], with_preview=True)
    expected = {f"workflow-count:{WORKSPACE}": 50_000,
                f"workflow-concurrency:{WORKSPACE}": 6,
                f"workflow-batch-size:{WORKSPACE}": 200,
                f"workflow-chunk-chars:{WORKSPACE}": 1500}
    for key, value in expected.items():
        app.number_input(key=key).set_value(value)
    app.run()
    for node in ("sft", "ingest", "package", "sft"):
        app.session_state[f"workflow-setup-node:{WORKSPACE}"] = node
        app.run()
        assert not app.exception
        for key, value in expected.items():
            matches = [widget for widget in app.number_input if widget.key == key]
            assert len(matches) == 1 and matches[0].value == value
        assert len([widget for widget in app.get("file_uploader")
                    if widget.key == f"workflow-upload:{WORKSPACE}:{DOCUMENTS}"]) == 1
        assert len([widget for widget in app.multiselect
                    if widget.key == f"workflow-sources:{WORKSPACE}:{DOCUMENTS}"]) == 1
        assert app.multiselect(key=f"workflow-sources:{WORKSPACE}:{DOCUMENTS}").value == [str(document)]
        assert_no_run(app)
