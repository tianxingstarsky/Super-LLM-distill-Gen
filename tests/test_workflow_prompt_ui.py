"""Node prompt editing, persistence, and immutable run inspection."""
from copy import deepcopy
from io import BytesIO
from types import SimpleNamespace

import pytest
from streamlit.testing.v1 import AppTest

from lib.domain.workflow_node_prompts import builtin_node_prompt, snapshot_node_prompts
from lib.presentation.streamlit import workflow_prompt_settings as settings
from lib.presentation.streamlit.workflow_reuse import recipe_to_draft
from tests.test_workflow_draft import SCRIPT


def test_ordinary_sft_prompt_is_node_local_and_survives_step_and_node_changes():
    ui = AppTest.from_string(SCRIPT).run()
    selection = "workflow-node-prompt-selection:fixture:sft"
    prompt = "workflow-node-prompt:fixture:sft:workflow.sft"
    assert ui.selectbox(key=selection).value == "workflow.sft"
    assert ui.text_area(key=prompt).value == builtin_node_prompt("workflow.sft")
    custom = 'Keep each answer grounded. Return {"answer":"full answer","reasoning":"brief basis","quotes":[]}.'
    ui.text_area(key=prompt).set_value(custom).run()
    ui.selectbox(key=selection).set_value("workflow.jev_score").run()
    quality = "workflow-node-prompt:fixture:sft:workflow.jev_score"
    assert ui.text_area(key=quality).value == builtin_node_prompt("workflow.jev_score")
    ui.pills(key="workflow-targets:fixture:自动推荐").set_value(["sft", "dpo"]).run()
    ui.button(key="fixture-node:preference").click().run()
    ui.button(key="fixture-node:sft").click().run()
    ui.selectbox(key=selection).set_value("workflow.sft").run()
    assert ui.text_area(key=prompt).value == custom
    assert ui.session_state["workflow-form-draft:fixture"][prompt] == custom
    ui.checkbox(key="fixture-show").uncheck().run()
    ui.checkbox(key="fixture-show").check().run()
    assert ui.text_area(key=prompt).value == custom
    assert not ui.exception


def test_style_toggle_switches_active_prompt_without_losing_ordinary_prompt():
    ui = AppTest.from_string(SCRIPT).run()
    prompt = "workflow-node-prompt:fixture:sft:workflow.sft"
    ui.text_area(key=prompt).set_value("Original distillation instructions.").run()
    ui.toggle(key="workflow-generation-enabled:fixture:sft").set_value(True).run()
    selection = ui.selectbox(key="workflow-node-prompt-selection:fixture:sft")
    assert selection.value == "workflow.sft_styled"
    assert "风格符合度核验" in selection.options
    styled = "workflow-node-prompt:fixture:sft:workflow.sft_styled"
    ui.text_area(key=styled).set_value("Write the requested styled answer.").run()
    ui.toggle(key="workflow-generation-enabled:fixture:sft").set_value(False).run()
    assert ui.selectbox(key=selection.key).value == "workflow.sft"
    assert ui.text_area(key=prompt).value == "Original distillation instructions."
    assert not ui.exception


def test_node_prompts_are_shown_only_for_actual_model_processing_steps():
    ui = AppTest.from_string(SCRIPT).run()
    ui.pills(key="workflow-targets:fixture:自动推荐").set_value(["cpt", "sft"]).run()
    ui.button(key="fixture-node:ingest").click().run()
    assert ui.selectbox(key="workflow-node-prompt-selection:fixture:ingest").value == "workflow.document_parse"
    ui.radio(key="workflow-document-parse-mode:fixture").set_value("native").run()
    assert not any(row.key.startswith("workflow-node-prompt:") for row in ui.text_area)
    ui.button(key="fixture-node:cpt").click().run()
    assert ui.selectbox(key="workflow-node-prompt-selection:fixture:cpt").value == "workflow.cpt_clean"
    ui.radio(key="workflow-cpt-processing-mode:fixture").set_value("native").run()
    assert not any(row.key.startswith("workflow-node-prompt:") for row in ui.text_area)
    ui.button(key="fixture-node:package").click().run()
    assert not any(row.key.startswith("workflow-node-prompt:") for row in ui.text_area)
    ui.segmented_control(key="workflow-source-mode:fixture").set_value("开放需求").run()
    assert ui.selectbox(key="workflow-node-prompt-selection:fixture:ingest").value == "workflow.plan"
    ui.pills(key="workflow-targets:fixture:自动推荐").set_value(["cot"]).run()
    ui.button(key="fixture-node:cot").click().run()
    assert ui.selectbox(key="workflow-node-prompt-selection:fixture:cot").value == "workflow.rationale_check"
    assert len(ui.selectbox(key="workflow-node-prompt-selection:fixture:cot").options) == 1
    assert not ui.exception


def test_empty_prompt_blocks_submission_until_restored():
    ui = AppTest.from_string(SCRIPT).run()
    prompt = "workflow-node-prompt:fixture:sft:workflow.sft"
    ui.text_area(key=prompt).set_value("  ").run()
    assert any("节点提示词尚未完成" in warning.value for warning in ui.warning)
    assert ui.button(key="workflow-create:fixture").disabled
    ui.button(key="workflow-node-prompt-reset:fixture:sft:workflow.sft").click().run()
    assert ui.text_area(key=prompt).value == builtin_node_prompt("workflow.sft")
    assert not any("节点提示词尚未完成" in warning.value for warning in ui.warning)
    assert ui.button(key="workflow-node-prompt-reset:fixture:sft:workflow.sft").disabled
    assert not ui.exception


def test_snapshot_excludes_inactive_node_and_step_edits(monkeypatch):
    values = {"workflow-node-prompt:w:sft:workflow.sft": "Ordinary instructions.",
              "workflow-node-prompt:w:sft:workflow.sft_styled": "Styled instructions.",
              "workflow-node-prompt:w:trim:workflow.trim": "Cleanup instructions.",
              "workflow-node-prompt:w:cpt:workflow.corpus": "Corpus instructions."}
    monkeypatch.setattr(settings, "st", SimpleNamespace(session_state={"workflow-form-draft:w": values}))
    assert settings.node_prompt_snapshot("w", ["sft", "cpt"], "文档资料") == {
        "sft": {"workflow.sft": "Ordinary instructions."}}
    assert settings.node_prompt_snapshot("w", ["sft"], "文档资料",
        node_generation={"sft": {"enabled": True}}) == {"sft": {"workflow.sft_styled": "Styled instructions."}}
    assert values["workflow-node-prompt:w:trim:workflow.trim"] == "Cleanup instructions."


@pytest.mark.parametrize("payload,name,message", [
    (b"\xff\xfe", "prompt.txt", "UTF-8"), (b"", "prompt.md", "为空"),
    (b"A" * (128 * 1024 + 1), "prompt.md", "128 KiB"),
    (b"A" * 32769, "prompt.md", "32,768"),
    (b"data\x00more", "prompt.txt", "无效字符"), (b"Keep facts.", "prompt.json", "TXT"),
], ids=["bad-encoding", "empty", "byte-limit", "character-limit", "null-character", "wrong-extension"])
def test_import_rejects_invalid_prompt_without_replacing_node_draft(monkeypatch, payload, name, message):
    upload = BytesIO(payload)
    upload.name = name
    key = "workflow-node-prompt:w:sft:workflow.sft"
    state = {"workflow-node-prompt-upload:w:sft:workflow.sft": upload, key: "Keep existing prompt."}
    monkeypatch.setattr(settings, "st", SimpleNamespace(session_state=state))
    saved = []
    settings._import_prompt("w", "sft", "workflow.sft", lambda *args: saved.append(args))
    assert message in state["workflow-node-prompt-upload-error:w:sft:workflow.sft"]
    assert state[key] == "Keep existing prompt."
    assert not saved


def test_import_preserves_literal_braces_and_saves_only_selected_node_step(monkeypatch):
    body = '\ufeff根据资料回答。返回 {"answer":"完整答案","reasoning":"依据"}。'
    upload = BytesIO(body.encode("utf-8"))
    upload.name = "prompt.MD"
    other = "workflow-node-prompt:w:sft:workflow.jev_score"
    state = {"workflow-node-prompt-upload:w:sft:workflow.sft": upload, other: "Independent review."}
    monkeypatch.setattr(settings, "st", SimpleNamespace(session_state=state))

    def save_field(workspace, key):
        state.setdefault(f"workflow-form-draft:{workspace}", {})[key] = state[key]

    settings._import_prompt("w", "sft", "workflow.sft", save_field)
    assert state["workflow-node-prompt:w:sft:workflow.sft"] == body.lstrip("\ufeff")
    assert state[other] == "Independent review."
    assert settings.node_prompt_snapshot("w", ["sft"], "文档资料")["sft"]["workflow.sft"] == body.lstrip("\ufeff")


def test_reusing_run_copies_actual_pinned_active_prompts_and_keeps_source_immutable():
    pinned = snapshot_node_prompts({"sft": {"workflow.sft": "Pinned writer instructions."}})
    pinned["sft"]["workflow.jev_score"] = "Previous built-in quality template."
    recipe = {"sources": [], "targets": ["sft"], "brief": "Generate examples.",
              "node_prompts": {"sft": {"workflow.sft": "Pinned writer instructions."}},
              "node_prompt_templates": pinned}
    original = deepcopy(recipe)
    values = recipe_to_draft(recipe, "New task", "w", [])["values"]
    assert values["workflow-node-prompt:w:sft:workflow.sft"] == "Pinned writer instructions."
    assert values["workflow-node-prompt:w:sft:workflow.jev_score"] == "Previous built-in quality template."
    assert not any(key.startswith("workflow-node-prompt:w:trim:") for key in values)
    assert "workflow-node-prompt:w:sft:workflow.sft_styled" not in values
    assert recipe == original


def test_legacy_recipe_can_copy_explicit_node_overrides_without_a_snapshot():
    recipe = {"sources": [], "targets": ["sft"],
              "node_prompts": {"sft": {"workflow.sft": "Use this writer prompt."}}}
    values = recipe_to_draft(recipe, "New task", "w", [])["values"]
    assert values["workflow-node-prompt:w:sft:workflow.sft"] == "Use this writer prompt."


def test_run_inspection_displays_pinned_body_read_only():
    script = '''
from lib.presentation.streamlit.workflow_prompt_settings import render_run_node_prompts
recipe = {'sources': [], 'node_prompt_templates': {'sft': {
    'workflow.sft': 'A pinned prompt with {literal JSON braces}.',
    'workflow.jev_score': 'A pinned quality prompt.'}}}
render_run_node_prompts('sft', recipe, 'run-fixture')
'''
    ui = AppTest.from_string(script).run()
    prompt = ui.text_area(key="workflow-run-prompt-body:run-fixture:sft:workflow.sft")
    assert prompt.disabled
    assert prompt.value == "A pinned prompt with {literal JSON braces}."
    ui.selectbox(key="workflow-run-prompt-selection:run-fixture:sft").set_value("workflow.jev_score").run()
    assert ui.text_area(key="workflow-run-prompt-body:run-fixture:sft:workflow.jev_score").value == "A pinned quality prompt."
    assert not ui.exception


def test_workbench_submits_active_node_prompt_overrides():
    script = SCRIPT.replace("def task_runs(self): return []", "def create_run(self, **values):\n"
                            "        st.session_state['fixture-created'] = values\n"
                            "        return 'fixture-run'\n"
                            "    def task_runs(self): return []")
    ui = AppTest.from_string(script).run()
    ui.pills(key="workflow-targets:fixture:自动推荐").set_value(["sft"]).run()
    ui.segmented_control(key="workflow-source-mode:fixture").set_value("开放需求").run()
    ui.text_area(key="workflow-open-brief:fixture").set_value("Generate maintenance examples.").run()
    ui.button(key="fixture-node:sft").click().run()
    ui.text_area(key="workflow-node-prompt:fixture:sft:workflow.sft").set_value("Use the selected sources to answer.").run()
    ui.button(key="workflow-create:fixture").click().run()
    assert ui.session_state["fixture-created"]["node_prompts"] == {
        "sft": {"workflow.sft": "Use the selected sources to answer."}}
    assert not ui.exception
