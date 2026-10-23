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
