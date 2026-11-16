"""Entry choices and actionable blockers through the actual workbench widgets."""
from copy import deepcopy

import pytest
from streamlit.testing.v1 import AppTest

from tests.test_workflow_draft import SCRIPT


MODE = "workflow-creation-mode:fixture"
SOURCE = "workflow-source-mode:fixture"
PRESET = "workflow-preset:fixture"
DIRECTOR = "workflow-director-enabled:fixture"


def _script(source):
    code = SCRIPT.replace("'fixture.txt'", repr(str(source)))
    code = code.replace("st.checkbox('Show workbench'", """class MemoryDraft:
    def load(self):
        return st.session_state.get('fixture-durable', {})
    def replace(self, values):
        from lib.domain.creation_draft import validate_creation_draft
        st.session_state['fixture-durable'] = validate_creation_draft(values)
        st.session_state['fixture-saves'] = st.session_state.get('fixture-saves', 0) + 1
    def save_snapshot(self, *args, **kwargs): return 'fixture'
st.checkbox('Show workbench'""")
    return code.replace("WorkflowNodeModelsApplication(Inventory()))",
                        "WorkflowNodeModelsApplication(Inventory()), draft_application=MemoryDraft())")


@pytest.mark.parametrize("entry", ["overview:文档资料", "overview-target:sft"])
def test_home_automatic_entry_replaces_saved_human_mode_after_restart(tmp_path, entry):
    source = tmp_path / "source.txt"
    source.write_text("Keep these source facts.", encoding="utf-8")
    code = _script(source).replace(
        "st.checkbox('Show workbench',value=True,key='fixture-show')\nif st.session_state['fixture-show']:",
        """from lib.presentation.streamlit.home_page import render_overview
class Home:
    def task_runs(self): return []
    def list_releases(self): return []
if st.session_state.get('nav', 'home') == 'home':
    render_overview(Home(), 'fixture', [], 0, 0, 'input', 'output',
        navigate=lambda route: st.session_state.update(nav=route), job_status=lambda: None)
else:""")
    ui = AppTest.from_string(code)
    ui.session_state["fixture-durable"] = {
        MODE: "人工问答增强", "workflow-human-enabled:fixture": True,
        SOURCE: "文档资料", PRESET: "自选目标",
        "workflow-targets:fixture:自选目标": ["sft"],
        "workflow-sources:fixture:文档资料": [str(source)]}
    ui.run().button(key=entry).click().run()
    assert not ui.exception
    assert ui.segmented_control(key=MODE).value == "自动生成"
    saved = deepcopy(ui.session_state["fixture-durable"])
    assert saved[MODE] == "自动生成"
    assert saved["workflow-human-enabled:fixture"] is False
    reopened = AppTest.from_string(code)
    reopened.session_state["nav"] = "自动工作流"
    reopened.session_state["fixture-durable"] = saved
    reopened.run()
    assert not reopened.exception
    assert reopened.segmented_control(key=MODE).value == "自动生成"
    assert reopened.multiselect(key="workflow-sources:fixture:文档资料").value == [str(source)]


def test_confirmed_dsh_sources_keep_director_bypass_after_navigation_and_restart(tmp_path, monkeypatch):
    import io
    import sys
    import streamlit as st
    from pathlib import Path
    from lib.bootstrap.conversation_import import conversation_import_application
    from tests.test_conversation_import import fake_converter

    script = fake_converter(tmp_path)
    importer = conversation_import_application(tmp_path, command=(sys.executable, str(script)))
    original = b"original DSH event log"
    preview = importer.preview("session.jsonl", original)
    source = Path(importer.confirm(preview["import_id"])[0]["path"])
    code = _script(source).replace("draft_application=MemoryDraft())",
        "draft_application=MemoryDraft(), conversation_importer=st.session_state['fixture-importer'])")
    uploaded = {"visible": True}
    def uploader(*args, **kwargs):
        if kwargs.get("key") == "conversation-import:fixture:file" and uploaded["visible"]:
            stream = io.BytesIO(original)
            stream.name = "session.jsonl"
            return stream
        return [] if kwargs.get("accept_multiple_files") else None
    monkeypatch.setattr(st, "file_uploader", uploader)
    ui = AppTest.from_string(code)
    ui.session_state["fixture-importer"] = importer
    ui.session_state["fixture-durable"] = {
        SOURCE: "Agent 上下文", PRESET: "自选目标",
        "workflow-targets:fixture:自选目标": ["sft"],
        "workflow-conversation-format:fixture": "DSH 会话库",
        "workflow-node-bindings:fixture": {"director": {}},
        "workflow-node-bindings:fixture:initialized": ["director:generation", "director:question"]}
    ui.run().button(key="conversation-import:fixture:confirm").click().run()
    assert not ui.exception
    assert not ui.button(key="workflow-create:fixture").disabled
    reference_key = "workflow-conversation-imports:fixture"
    assert ui.session_state["fixture-durable"][reference_key] == {str(source): preview["import_id"]}
    uploaded["visible"] = False
    ui.checkbox(key="fixture-show").uncheck().run()
    ui.checkbox(key="fixture-show").check().run()
    assert not ui.exception
    assert "conversation-import:fixture:confirmed" not in ui.session_state
    assert not ui.button(key="workflow-create:fixture").disabled
    saved = deepcopy(ui.session_state["fixture-durable"])
    reopened = AppTest.from_string(code)
    reopened.session_state["fixture-durable"] = saved
    reopened.session_state["fixture-importer"] = conversation_import_application(tmp_path, command=())
    reopened.run()
    assert not reopened.exception
    assert not any(button.key == "fixture-node:director" for button in reopened.button)
    assert not reopened.button(key="workflow-create:fixture").disabled
    assert script.with_suffix(".calls").read_text().splitlines() == ["called"]
    source.write_bytes(source.read_bytes().replace(b"Disconnect", b"Connect"))
    reopened.run()
    assert not reopened.exception
    assert reopened.button(key="workflow-create:fixture").disabled
    assert any("导入确认记录未能恢复" in item.value for item in reopened.error)
    assert reopened.multiselect(key="workflow-sources:fixture:Agent 上下文").value == [str(source)]


def test_mode_roundtrip_restores_both_choices_after_navigation_and_restart(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("Keep these source facts.", encoding="utf-8")
    code = _script(source)
    ui = AppTest.from_string(code).run()
    ui.selectbox(key=PRESET).set_value("自选目标").run()
    ui.pills(key="workflow-targets:fixture:自选目标").set_value(["cpt", "sft", "dpo"]).run()
    ui.multiselect(key="workflow-sources:fixture:文档资料").set_value([str(source)]).run()
    ui.segmented_control(key=MODE).set_value("人工问答增强").run()
    assert not ui.exception
    assert ui.segmented_control(key=SOURCE).value == "开放需求"
    assert ui.pills(key="workflow-targets:fixture:自选目标").value == ["sft", "dpo"]
    ui.pills(key="workflow-targets:fixture:自选目标").set_value(["multiturn"]).run()
    ui.segmented_control(key=SOURCE).set_value("文档资料").run()
    ui.segmented_control(key=MODE).set_value("自动生成").run()
    assert not ui.exception
    assert ui.segmented_control(key=SOURCE).value == "文档资料"
    assert ui.pills(key="workflow-targets:fixture:自选目标").value == ["cpt", "sft", "dpo"]
    assert ui.multiselect(key="workflow-sources:fixture:文档资料").value == [str(source)]
    assert not ui.toggle(key=DIRECTOR).value
    ui.checkbox(key="fixture-show").uncheck().run()
    ui.checkbox(key="fixture-show").check().run()
    ui.segmented_control(key=MODE).set_value("人工问答增强").run()
    assert ui.pills(key="workflow-targets:fixture:自选目标").value == ["multiturn"]
    assert ui.segmented_control(key=SOURCE).value == "文档资料"
    assert ui.toggle(key=DIRECTOR).value
    saved = deepcopy(ui.session_state["fixture-durable"])
    fresh = AppTest.from_string(code)
    fresh.session_state["fixture-durable"] = saved
    fresh.run()
    assert not fresh.exception
    assert fresh.segmented_control(key=MODE).value == "人工问答增强"
    fresh.segmented_control(key=MODE).set_value("自动生成").run()
    assert not fresh.exception
    assert fresh.pills(key="workflow-targets:fixture:自选目标").value == ["cpt", "sft", "dpo"]
    assert fresh.segmented_control(key=SOURCE).value == "文档资料"
    assert fresh.multiselect(key="workflow-sources:fixture:文档资料").value == [str(source)]


def test_unchanged_rerender_does_not_resave_draft_but_failed_save_retries(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("Keep these facts.", encoding="utf-8")
    code = _script(source).replace(
        "from lib.domain.creation_draft import validate_creation_draft",
        "if st.session_state.get('fixture-fail-save'): raise OSError('controlled failure')\n"
        "        from lib.domain.creation_draft import validate_creation_draft")
    ui = AppTest.from_string(code).run()
    before = ui.session_state["fixture-saves"]
    ui.run()
    assert not ui.exception
    assert ui.session_state["fixture-saves"] == before
    ui.session_state["fixture-fail-save"] = True
    ui.text_input(key="workflow-name:fixture").set_value("Unsaved work").run()
    assert ui.session_state["workflow-draft-error:fixture"]
    assert ui.session_state["fixture-durable"]["workflow-name:fixture"] != "Unsaved work"
    ui.session_state["fixture-fail-save"] = False
    ui.run()
    assert not ui.exception
    assert ui.session_state["fixture-durable"]["workflow-name:fixture"] == "Unsaved work"
    assert "workflow-draft-error:fixture" not in ui.session_state


@pytest.mark.parametrize("mode", ["native", "model", "docling"])
def test_image_blocker_opens_input_settings_for_every_nonvision_parser(tmp_path, mode):
    source = tmp_path / "source.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\n")
    ui = AppTest.from_string(_script(source))
    ui.session_state["workflow-document-parse-mode:fixture"] = mode
    ui.run()
    ui.multiselect(key="workflow-sources:fixture:文档资料").set_value([str(source)]).run()
    assert not ui.exception
    assert ui.button(key="workflow-create:fixture").disabled
    issue = ui.button(key="workflow-config-fix:fixture:image-parser:ingest")
    assert "图片需要多模态识别" in issue.label
    issue.click().run()
    assert not ui.exception
    assert ui.session_state["workflow-setup-node:fixture"] == "ingest"
    assert ui.session_state["canvas-open:setup-canvas:fixture"] is True
    assert ui.radio(key="workflow-document-parse-mode:fixture").value == mode


def test_recorded_conversations_bypass_director_without_forgetting_document_settings(tmp_path):
    source = tmp_path / "recorded.jsonl"
    source.write_text('{"messages": [{"role": "user", "content": "Hello"}, '
                      '{"role": "assistant", "content": "Hello!"}]}\n', encoding="utf-8")
    ui = AppTest.from_string(_script(source)).run()
    ui.toggle(key=DIRECTOR).set_value(True).run()
    ui.button(key="fixture-node:director").click().run()
    ui.selectbox(key="node-model:fixture:director:generation:backend").set_value(None).run()
    ui.selectbox(key="node-model:fixture:director:question:backend").set_value(None).run()
    assert ui.button(key="workflow-create:fixture").disabled
    ui.session_state["conversation-import:fixture:confirmed"] = {
        "target": "sft", "rows": [{"path": str(source)}]}
    ui.segmented_control(key=SOURCE).set_value("Agent 上下文").run()
    ui.multiselect(key="workflow-sources:fixture:Agent 上下文").set_value([str(source)]).run()
    assert not ui.exception
    assert not any(button.key == "fixture-node:director" for button in ui.button)
    assert not any(widget.key == DIRECTOR for widget in ui.toggle)
    assert not any(button.key.startswith("workflow-config-fix:fixture:model:director") for button in ui.button)
    assert not ui.button(key="workflow-create:fixture").disabled
    assert ui.session_state["workflow-form-draft:fixture"][DIRECTOR] is True
    assert ui.session_state["workflow-node-bindings:fixture"]["sft"]["generation"]
    ui.selectbox(key=PRESET).set_value("多轮对话").run()
    assert not any(widget.key == "workflow-turns:fixture" for widget in ui.number_input)
    ui.segmented_control(key=SOURCE).set_value("文档资料").run()
    assert not ui.exception
    assert ui.toggle(key=DIRECTOR).value
    assert any(button.key == "fixture-node:director" for button in ui.button)
    assert any(button.key.startswith("workflow-config-fix:fixture:model:director") for button in ui.button)
    assert ui.number_input(key="workflow-turns:fixture")


def test_mixed_or_unconfirmed_json_does_not_skip_document_guidance(tmp_path):
    recorded = tmp_path / "recorded.jsonl"
    other = tmp_path / "document.jsonl"
    recorded.write_text('{"messages": []}\n', encoding="utf-8")
    other.write_text('{"text": "Document facts."}\n', encoding="utf-8")
    code = _script(recorded).replace("'label':'Fixture source'}]",
        "'label':'Fixture source'}, {'path':" + repr(str(other)) + ",'label':'Other source'}]")
    ui = AppTest.from_string(code).run()
    ui.toggle(key=DIRECTOR).set_value(True).run()
    ui.segmented_control(key=SOURCE).set_value("Agent 上下文").run()
    ui.multiselect(key="workflow-sources:fixture:Agent 上下文").set_value([str(recorded)]).run()
    assert any(button.key == "fixture-node:director" for button in ui.button)
    ui.session_state["conversation-import:fixture:confirmed"] = {
        "target": "agent", "rows": [{"path": str(recorded)}]}
    ui.run()
    assert any(button.key == "fixture-node:director" for button in ui.button)
    ui.session_state["conversation-import:fixture:confirmed"] = {
        "target": "sft", "rows": [{"path": str(recorded)}]}
    ui.multiselect(key="workflow-sources:fixture:Agent 上下文").set_value([str(recorded), str(other)]).run()
    assert not ui.exception
    assert any(button.key == "fixture-node:director" for button in ui.button)
    ui.multiselect(key="workflow-sources:fixture:Agent 上下文").set_value([str(recorded)]).run()
    assert not ui.exception
    assert not any(button.key == "fixture-node:director" for button in ui.button)


def test_adjacent_node_controls_keep_the_draft_and_stop_at_graph_boundaries(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("The corpus is reused by the dialogue generator.", encoding="utf-8")
    ui = AppTest.from_string(_script(source)).run()
    ui.selectbox(key=PRESET).set_value("自选目标").run()
    ui.pills(key="workflow-targets:fixture:自选目标").set_value(["cpt", "sft"]).run()
    ui.multiselect(key="workflow-sources:fixture:文档资料").set_value([str(source)]).run()
    ui.text_input(key="workflow-name:fixture").set_value("Keep my task name").run()
    ui.button(key="fixture-node:ingest").click().run()
    bindings = deepcopy(ui.session_state["workflow-node-bindings:fixture"])
    assert ui.button(key="workflow-previous-node:fixture").disabled
    ui.button(key="workflow-next-node:fixture").click().run()
    assert not ui.exception
    assert ui.session_state["workflow-setup-node:fixture"] == "cpt"
    assert ui.session_state["canvas-open:setup-canvas:fixture"]
    ui.button(key="workflow-previous-node:fixture").click().run()
    assert ui.session_state["workflow-setup-node:fixture"] == "ingest"
    assert ui.session_state["workflow-node-bindings:fixture"] == bindings
    assert ui.text_input(key="workflow-name:fixture").value == "Keep my task name"
    ui.button(key="fixture-node:package").click().run()
    assert not ui.exception
    assert ui.button(key="workflow-next-node:fixture").disabled


def test_cot_mode_link_opens_prompt_controls_without_enabling_authoring(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("A source for reasoning configuration.", encoding="utf-8")
    ui = AppTest.from_string(_script(source)).run()
    ui.selectbox(key=PRESET).set_value("自选目标").run()
    ui.pills(key="workflow-targets:fixture:自选目标").set_value(["cot"]).run()
    bindings = deepcopy(ui.session_state["workflow-node-bindings:fixture"])
    ui.button(key="workflow-cot-mode:fixture").click().run()
    assert not ui.exception
    assert ui.session_state["workflow-setup-node:fixture"] == "cot"
    assert ui.session_state["workflow-node-tabs:fixture:cot"] == "提示词与风格"
    assert not ui.toggle(key="workflow-generation-enabled:fixture:cot").value
    assert ui.session_state["workflow-node-bindings:fixture"] == bindings
    ui.toggle(key="workflow-generation-enabled:fixture:cot").set_value(True).run()
    assert not ui.exception
    assert not any(button.key == "workflow-cot-mode:fixture" for button in ui.button)
